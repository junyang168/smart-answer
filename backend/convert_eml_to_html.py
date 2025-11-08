# Parse the uploaded .eml file, extract the HTML body, and save it as an .html file
from email import policy
from email.parser import BytesParser
from pathlib import Path

eml_path = Path("/Users/junyang/Downloads/聖道教會2025年9月14日主日崇拜.eml")
out_path = Path("/opt/homebrew/var/www/church/web/data/sunday_worship/sunday_service_reminder.html")

with open(eml_path, "rb") as f:
    msg = BytesParser(policy=policy.default).parse(f)

html_parts = []

if msg.is_multipart():
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "text/html":
            # get_content() decodes quoted-printable/base64 per policy.default
            html_parts.append(part.get_content())
else:
    if msg.get_content_type() == "text/html":
        html_parts.append(msg.get_content())

html_content = "\n".join(html_parts).strip()

# Save to file
out_path.write_text(html_content, encoding="utf-8")
