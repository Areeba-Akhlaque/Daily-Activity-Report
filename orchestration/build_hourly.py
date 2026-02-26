import os
import json
import random
import hashlib

def distribute_counts(member, date, platform, action, total_count):
    # A simple deterministic hash to spread hours consistently
    seed_str = f"{member}_{date}_{platform}_{action}"
    # Python hash is randomized per session by default, use hashlib for determinism
    stable_hash = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)
    random.seed(stable_hash)
    
    # Core working hours 8 AM to 6 PM (8 to 18)
    hours = list(range(8, 19))
    records = []
    
    for _ in range(total_count):
        # Slightly favor middle of the day (11 AM to 3 PM)
        weights = [1 if (h < 11 or h > 15) else 3 for h in hours]
        h = random.choices(hours, weights=weights, k=1)[0]
        
        records.append({
            'member': member,
            'date': date,
            'hour': h,
            'platform': platform,
            'type': action
        })
    return records

def generate_hourly():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)
    dash_dir = os.path.join(root_dir, 'dashboard')
    
    data_path = os.path.join(dash_dir, 'data.json')
    out_path = os.path.join(dash_dir, 'hourly_data.json')
    
    if not os.path.exists(data_path):
        print("No data.json found.")
        return
        
    with open(data_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    audit_data = data.get('dailyAudit', [])
    hourly_records = []
    
    for row in audit_data:
        c = int(row.get('Count', 0))
        if c > 0:
            member = row.get('Team Member', 'Unknown')
            date = row.get('Activity Date')
            platform = row.get('Platform')
            act = row.get('Activity Type')
            
            recs = distribute_counts(member, date, platform, act, c)
            hourly_records.extend(recs)
            
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(hourly_records, f, indent=2)
        
    print(f"Successfully generated {len(hourly_records)} hourly events to {out_path}.")

if __name__ == "__main__":
    generate_hourly()
