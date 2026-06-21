import csv, io
from app.models import Lead

def leads_to_csv(leads: list[Lead]) -> str:
    output = io.StringIO()
    fields = ["id","lead_code","name","niche","city","country","website_url","instagram_url","telegram_url","email","phone","whatsapp","score","status","source_url","notes"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for lead in leads:
        writer.writerow({f: getattr(lead, f) for f in fields})
    return output.getvalue()
