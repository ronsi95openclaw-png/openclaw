#!/usr/bin/env python3
"""
HaulYA'LL! Operations Sub-Agent
Handles: lead intake, content generation, job logging
All output is DRAFT -- requires Ronnie approval before posting
"""
import json
from datetime import datetime
from pathlib import Path

BRAND = {
    "name": "Haul Y'all",
    "phone": "(469) 618-7677",
    "email": "junkgone@haulya-ll.com",
    "hours": "Mon-Sat 7am-7pm",
    "area": "DFW Metro"
}

OUTPUT_DIR = Path("sub-agents/haulyall-agent/output")

def generate_social_post(job_description: str, job_type: str = "haul") -> dict:
    """Generate draft social post -- never auto-posts"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    post = {
        "timestamp": datetime.now().isoformat(),
        "status": "DRAFT -- AWAITING RONNIE APPROVAL",
        "job_type": job_type,
        "job_description": job_description,
        "platforms": {
            "facebook": {
                "caption": f"Another successful haul in DFW!\n\n{job_description}\n\nNeed junk gone?\nCall {BRAND['phone']}\nEmail {BRAND['email']}\nHours {BRAND['hours']}",
                "hashtags": "#HaulYall #DFWJunkRemoval #JunkRemoval #DFW #Texas"
            },
            "instagram": {
                "caption": f"Haul Y'all on the move!\n\n{job_description}\n\n{BRAND['phone']}\n{BRAND['area']} | {BRAND['hours']}",
                "hashtags": "#HaulYall #DFWJunkRemoval #Dallas #FortWorth #Texas"
            }
        }
    }

    out = OUTPUT_DIR / f"post_draft_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out, 'w') as f:
        json.dump(post, f, indent=2)

    print(f"Draft saved: {out}")
    print("AWAITING RONNIE APPROVAL -- not posted")
    return post

def log_job(details: dict) -> None:
    jobs_file = OUTPUT_DIR / "jobs_log.json"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    jobs = json.loads(jobs_file.read_text()) if jobs_file.exists() else []
    jobs.append({**details, "timestamp": datetime.now().isoformat()})
    jobs_file.write_text(json.dumps(jobs, indent=2))
    print(f"Job logged. Total: {len(jobs)}")

if __name__ == "__main__":
    generate_social_post(
        job_description="Cleared out a full garage in Irving -- 2 truckloads of old furniture and appliances!",
        job_type="garage_cleanout"
    )
