import json
import imaplib
import email
from email.header import decode_header
import re
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

def decode_mime_words(s):
    if not s:
        return ""
    try:
        parts = decode_header(s)
        decoded = []
        for word, encoding in parts:
            if isinstance(word, bytes):
                decoded.append(word.decode(encoding or 'utf-8', errors='ignore'))
            else:
                decoded.append(str(word))
        return "".join(decoded)
    except Exception:
        return str(s)

def parse_body(msg):
    body = ""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                if content_type == "text/plain" and "attachment" not in content_disposition:
                    payload = part.get_payload(decode=True)
                    if payload:
                        body = payload.decode(errors='ignore')
                        break
                elif content_type == "text/html" and "attachment" not in content_disposition:
                    payload = part.get_payload(decode=True)
                    if payload:
                        html = payload.decode(errors='ignore')
                        body = re.sub(r'<[^<]+?>', '', html) # Quitar etiquetas html rudimentariamente
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                body = payload.decode(errors='ignore')
    except Exception as e:
        body = f"[Error leyendo cuerpo: {str(e)}]"
    return body.strip()

def fetch_real_emails(host, email_addr, password):
    print(f"[*] Conectando a {host} para {email_addr}...")
    mail = imaplib.IMAP4_SSL(host, 993)
    mail.login(email_addr, password)
    mail.select("inbox")
    
    # Intentar buscar correos desde el 23-Jul-2026
    # Si la fecha es inválida o el servidor no tiene correos tan nuevos, buscamos todos
    try:
        status, messages = mail.search(None, '(SINCE "23-Jul-2026")')
        email_ids = messages[0].split()
    except Exception:
        email_ids = []
        
    if not email_ids:
        print("[!] No se encontraron correos desde el 23-Jul-2026. Jalando últimos 15 correos de la bandeja...")
        status, messages = mail.search(None, 'ALL')
        email_ids = messages[0].split()
        
    emails_list = []
    # Jalamos los últimos 15 correos
    for mail_id in reversed(email_ids[-15:]):
        res, msg_data = mail.fetch(mail_id, "(RFC822)")
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                
                subject = decode_mime_words(msg["Subject"])
                sender = decode_mime_words(msg["From"])
                date_str = msg["Date"]
                body = parse_body(msg)
                
                # Identificar expediente con regex (ej. 14820/2026, 1234/26)
                exp_match = re.search(r'\b\d+/\d{2,4}(?:-\d+)*\b', subject + " " + body)
                expediente = exp_match.group(0) if exp_match else "Sin Expediente"
                
                # Buscar cliente registrado en el asunto o cuerpo
                cliente = "Externo / No Registrado"
                for possible_client in ["NEXTMED", "ASPID", "EEE", "MARLEX-HC"]:
                    if possible_client.lower() in (subject + " " + body).lower():
                        cliente = possible_client
                        break
                
                # Intentar limpiar la fecha
                time_str = "Reciente"
                try:
                    clean_date = re.sub(r'\s*[\+\-]\d{4}.*$', '', date_str).strip()
                    # A veces las fechas traen el nombre del día, lo quitamos si es necesario
                    if "," in clean_date:
                        clean_date = clean_date.split(",", 1)[1].strip()
                    # Formatos comunes: "23 Jul 2026 16:48:00"
                    # Reemplazamos meses abreviados a números para simplificar o usar parser local
                    parsed_date = None
                    for fmt in ["%d %b %Y %H:%M:%S", "%d %b %Y %H:%M"]:
                        try:
                            parsed_date = datetime.strptime(clean_date, fmt)
                            break
                        except ValueError:
                            continue
                    if parsed_date:
                        time_str = parsed_date.strftime("%d/%b/%Y %H:%M")
                    else:
                        time_str = date_str
                except Exception:
                    time_str = date_str if date_str else "Desconocida"
                
                # Detectar urgencia
                urgente = any(kw in (subject + " " + body).lower() for kw in ["urgente", "plazo", "requerimiento", "citacion", "termino", "multa", "sancion", "acuerdo"])
                
                badge = "tfja"
                badge_text = "Notificación"
                if "oic" in sender.lower() or "funcion publica" in sender.lower() or "sfp" in sender.lower():
                    badge = "dof"
                    badge_text = "Procedimiento"
                elif "suprema corte" in sender.lower() or "scjn" in sender.lower() or "tribunal" in sender.lower() or "juzgado" in sender.lower():
                    badge = "scjn"
                    badge_text = "Judicial"
                
                emails_list.append({
                    "id": mail_id.decode(),
                    "sender": sender,
                    "badge": badge,
                    "badgeText": badge_text,
                    "subject": subject,
                    "time": time_str,
                    "dateRaw": date_str,
                    "expediente": expediente,
                    "cliente": cliente,
                    "cuerpo": body[:300] + ("..." if len(body) > 300 else ""),
                    "urgente": urgente,
                    "deadlineDays": 3 if urgente else 0,
                    "actionText": "Computar Plazo de Desahogo" if urgente else "Ver Detalle",
                    "actionType": "plazo" if urgente else "redactar",
                    "read": False
                })
                
    mail.logout()
    print(f"[+] Sincronizados exitosamente {len(emails_list)} correos.")
    return emails_list

class EmailProxyHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header("Access-Control-Allow-Headers", "X-Requested-With, Content-Type")
        self.end_headers()

    def do_POST(self):
        if self.path == '/get-emails':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                params = json.loads(post_data.decode('utf-8'))
                email_addr = params.get('email')
                password = params.get('password')
                provider = params.get('provider', 'outlook')
                
                # Configurar IMAP Host según el proveedor
                imap_host = "imap-mail.outlook.com"
                if "gmail" in provider.lower() or "google" in provider.lower():
                    imap_host = "imap.gmail.com"
                
                emails = fetch_real_emails(imap_host, email_addr, password)
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                
                response_payload = json.dumps({"success": True, "emails": emails})
                self.wfile.write(response_payload.encode('utf-8'))
                
            except Exception as e:
                print(f"[Error] Falló la sincronización: {str(e)}")
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                
                response_payload = json.dumps({"success": False, "error": str(e)})
                self.wfile.write(response_payload.encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

def run(port=5000):
    server_address = ('127.0.0.1', port)
    httpd = HTTPServer(server_address, EmailProxyHandler)
    print(f"[*] Servidor de Sincronización PROTOLEGAL corriendo en http://127.0.0.1:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Apagando servidor de sincronización.")
        httpd.server_close()

if __name__ == '__main__':
    run()
