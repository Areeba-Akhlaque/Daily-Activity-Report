import os
import pandas as pd
from datetime import datetime
import pytz

# Logic constants
SESSION_GAP_MINUTES = 120 
BUFFER_MINUTES = 30  

def calculate_hours(times):
    if not times: return 0
    times = sorted(times)
    sessions = []
    current_session = [times[0]]
    for i in range(1, len(times)):
        gap = (times[i] - times[i-1]).total_seconds() / 60.0
        if gap > SESSION_GAP_MINUTES:
            sessions.append(current_session)
            current_session = [times[i]]
        else:
            current_session.append(times[i])
    sessions.append(current_session)
    
    total_sec = 0
    for s in sessions:
        dur = (s[-1] - s[0]).total_seconds() + (BUFFER_MINUTES * 60)
        total_sec += dur
    return total_sec / 3600.0

# Mock some data or try to read from a local source if possible
# Since I can't easily fetch raw logs without API, I'll rely on the logic proof.
# But I can try to find where the events are stored locally (if any)
# Actually, let's just wait for the command to finish or try to run a smaller subset.

# Let's try to run generate_activity_time.py but ONLY for Bilal for Feb 20.
# I'll edit generate_activity_time.py temporarily to print Bilal's stats.
