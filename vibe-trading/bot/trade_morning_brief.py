import datetime

def generate_brief():
    now = datetime.datetime.now()
    ny_kz_start = now.replace(hour=8, minute=30, second=0, microsecond=0)
    ny_kz_end = now.replace(hour=11, minute=0, second=0, microsecond=0)
    london_kz_start = now.replace(hour=2, minute=0, second=0, microsecond=0)
    london_kz_end = now.replace(hour=5, minute=0, second=0, microsecond=0)
    
    brief = {
        "timestamp": str(now),
        "kill_zones": {
            "ny": {
                "active": ny_kz_start <= now <= ny_kz_end,
                "next": "8:30-11 AM ET" if now < ny_kz_start else "tomorrow"
            },
            "london": {
                "active": london_kz_start <= now <= london_kz_end,
                "completed": now > london_kz_end
            }
        },
        "lucid_limits": {
            "max_drawdown": 1500,
            "max_trades": 10,
            "flatten_time": "4 PM ET"
        },
        "paper_mode": True
    }
    return brief

if __name__ == "__main__":
    print(generate_brief())