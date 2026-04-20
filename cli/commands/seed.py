"""
Seed command: generate realistic fake NGO data and upload to primary bucket.
Uses the Faker library to produce 15-20 files across donors/, reports/, projects/, finance/.
Each file is kept under 50 KB to stay within Free Tier limits.
"""
import os
import sys
import io
import json
import csv
import random
from datetime import datetime, timedelta, timezone, date

import click
from faker import Faker
from tqdm import tqdm

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from infra.config import config
from cli.utils import primary_s3, human_size

fake = Faker()
Faker.seed(42)

NGO_COUNTRIES = ["Kenya", "Bangladesh", "Colombia", "Ethiopia", "Myanmar",
                 "Nepal", "Haiti", "Uganda", "Cambodia", "Bolivia"]
PROGRAM_TYPES = ["Water & Sanitation", "Education", "Health", "Livelihoods",
                 "Food Security", "Emergency Response", "Gender Equality"]
CURRENCIES = ["USD", "EUR", "GBP", "KES", "BDT"]
DEPARTMENTS = ["Programs", "Finance", "HR", "Field Operations", "Fundraising", "Communications"]


def _csv_bytes(rows: list, fieldnames: list) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _json_bytes(data) -> bytes:
    return json.dumps(data, indent=2, default=str).encode("utf-8")


# ---- File generators -------------------------------------------------------

def gen_donor_list(year: int) -> tuple[str, bytes]:
    rows = []
    for _ in range(random.randint(40, 80)):
        rows.append({
            "donor_id": fake.uuid4()[:8].upper(),
            "name": fake.name(),
            "email": fake.email(),
            "country": fake.country(),
            "donation_amount": round(random.uniform(25, 5000), 2),
            "currency": random.choice(CURRENCIES),
            "date": fake.date_between(start_date=date(year, 1, 1), end_date=date(year, 12, 31)).isoformat(),
            "recurring": random.choice([True, False]),
        })
    return f"donors/donor_list_{year}.csv", _csv_bytes(rows, list(rows[0].keys()))


def gen_major_donors() -> tuple[str, bytes]:
    donors = []
    for _ in range(random.randint(5, 15)):
        donors.append({
            "donor_id": fake.uuid4()[:8].upper(),
            "name": fake.name(),
            "organization": fake.company(),
            "email": fake.email(),
            "phone": fake.phone_number(),
            "country": fake.country(),
            "total_given_usd": round(random.uniform(10000, 500000), 2),
            "giving_history": [
                {"year": y, "amount_usd": round(random.uniform(5000, 50000), 2)}
                for y in range(2021, 2025)
            ],
            "preferred_program": random.choice(PROGRAM_TYPES),
        })
    return "donors/major_donors.json", _json_bytes(donors)


def gen_field_report(country: str, date: str) -> tuple[str, bytes]:
    paragraphs = [fake.paragraph(nb_sentences=random.randint(4, 7)) for _ in range(3)]
    content = (
        f"FIELD REPORT — {country.upper()}\n"
        f"Date: {date}\n"
        f"Author: {fake.name()}, {fake.job()}\n"
        f"Program: {random.choice(PROGRAM_TYPES)}\n\n"
        + "\n\n".join(paragraphs)
        + f"\n\nBeneficiaries reached: {random.randint(50, 5000)}\n"
        f"Budget utilised (%): {random.randint(60, 99)}\n"
        f"Next steps: {fake.sentence()}\n"
    )
    safe_country = country.replace(" ", "_").lower()
    return f"reports/field_report_{safe_country}_{date}.txt", content.encode("utf-8")


def gen_quarterly_summary(quarter: int, year: int) -> tuple[str, bytes]:
    rows = []
    for country in random.sample(NGO_COUNTRIES, 5):
        for program in random.sample(PROGRAM_TYPES, 2):
            rows.append({
                "quarter": f"Q{quarter} {year}",
                "country": country,
                "program": program,
                "beneficiaries_reached": random.randint(100, 10000),
                "budget_usd": round(random.uniform(5000, 100000), 2),
                "spent_usd": round(random.uniform(3000, 95000), 2),
                "outcome_score": round(random.uniform(3.0, 5.0), 1),
            })
    return (
        f"reports/quarterly_summary_Q{quarter}_{year}.csv",
        _csv_bytes(rows, list(rows[0].keys())),
    )


def gen_active_projects() -> tuple[str, bytes]:
    projects = []
    for _ in range(random.randint(6, 12)):
        start = fake.date_between(start_date="-2y", end_date="today")
        end = start + timedelta(days=random.randint(180, 730))
        projects.append({
            "project_id": fake.uuid4()[:8].upper(),
            "name": f"{random.choice(PROGRAM_TYPES)} in {random.choice(NGO_COUNTRIES)}",
            "country": random.choice(NGO_COUNTRIES),
            "program_type": random.choice(PROGRAM_TYPES),
            "budget_usd": round(random.uniform(20000, 500000), 2),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "status": random.choice(["Active", "Active", "Active", "Completed", "On Hold"]),
            "beneficiaries_target": random.randint(500, 50000),
        })
    return "projects/active_projects.json", _json_bytes(projects)


def gen_grant_application(grant_id: str) -> tuple[str, bytes]:
    grant = {
        "grant_id": grant_id,
        "title": f"{random.choice(PROGRAM_TYPES)} Initiative — {random.choice(NGO_COUNTRIES)}",
        "donor_organization": fake.company(),
        "submission_date": fake.date_between(start_date="-1y", end_date="today").isoformat(),
        "requested_amount_usd": round(random.uniform(50000, 1000000), 2),
        "project_duration_months": random.choice([12, 18, 24, 36]),
        "country": random.choice(NGO_COUNTRIES),
        "program_type": random.choice(PROGRAM_TYPES),
        "narrative": fake.paragraph(nb_sentences=8),
        "budget_breakdown": [
            {"category": dept, "amount_usd": round(random.uniform(5000, 80000), 2)}
            for dept in random.sample(DEPARTMENTS, 4)
        ],
        "expected_beneficiaries": random.randint(1000, 100000),
        "status": random.choice(["Submitted", "Under Review", "Approved", "Rejected"]),
    }
    return f"projects/grant_application_{grant_id}.json", _json_bytes(grant)


def gen_monthly_expenses(month: int, year: int) -> tuple[str, bytes]:
    rows = []
    for _ in range(random.randint(20, 50)):
        rows.append({
            "expense_id": fake.uuid4()[:8].upper(),
            "date": fake.date_between(
                start_date=date(year, month, 1),
                end_date=date(year, month, 28),
            ).isoformat(),
            "category": random.choice(["Staff", "Travel", "Supplies", "Training", "Utilities", "Rent"]),
            "department": random.choice(DEPARTMENTS),
            "description": fake.sentence(nb_words=6),
            "amount_usd": round(random.uniform(10, 5000), 2),
            "approved_by": fake.name(),
        })
    return (
        f"finance/monthly_expenses_{month:02d}_{year}.csv",
        _csv_bytes(rows, list(rows[0].keys())),
    )


def gen_annual_budget(year: int) -> tuple[str, bytes]:
    budget = {
        "year": year,
        "total_budget_usd": round(random.uniform(500000, 5000000), 2),
        "currency": "USD",
        "approved_date": f"{year}-01-15",
        "allocations": [
            {
                "department": dept,
                "allocated_usd": round(random.uniform(50000, 800000), 2),
                "programs": [random.choice(PROGRAM_TYPES) for _ in range(2)],
            }
            for dept in DEPARTMENTS
        ],
    }
    return f"finance/annual_budget_{year}.json", _json_bytes(budget)


# ---- Main seed logic -------------------------------------------------------

def build_file_list() -> list[tuple[str, bytes]]:
    files = []
    year = 2024

    files.append(gen_donor_list(year))
    files.append(gen_donor_list(year - 1))
    files.append(gen_major_donors())

    for country in random.sample(NGO_COUNTRIES, 3):
        date = fake.date_between(start_date="-1y", end_date="today").isoformat()
        files.append(gen_field_report(country, date))

    for q in [1, 2, 3, 4]:
        files.append(gen_quarterly_summary(q, year))

    files.append(gen_active_projects())
    for _ in range(2):
        files.append(gen_grant_application(fake.uuid4()[:8].upper()))

    for month in random.sample(range(1, 13), 3):
        files.append(gen_monthly_expenses(month, year))

    files.append(gen_annual_budget(year))
    files.append(gen_annual_budget(year - 1))

    return files


@click.command()
def seed():
    """Generate fake NGO data and upload to the primary S3 bucket."""
    s3 = primary_s3()
    files = build_file_list()

    click.echo(f"Generated {len(files)} files. Uploading to s3://{config.primary_bucket}/\n")

    uploaded = 0
    with tqdm(total=len(files), desc="Uploading", unit="file") as pbar:
        for s3_key, content in files:
            size = len(content)
            if size > config.MAX_FILE_BYTES:
                content = content[: config.MAX_FILE_BYTES]
            s3.put_object(
                Bucket=config.primary_bucket,
                Key=s3_key,
                Body=content,
            )
            tqdm.write(f"  OK  {s3_key}  ({human_size(size)})")
            uploaded += 1
            pbar.update(1)

    click.echo(f"\nSeed complete: {uploaded} files uploaded to s3://{config.primary_bucket}/")
    click.echo("The Replicator Lambda will replicate each file to the backup bucket automatically.")
