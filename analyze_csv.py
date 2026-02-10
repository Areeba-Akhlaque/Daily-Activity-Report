import pandas as pd

# Read first 100 rows to see structure
df = pd.read_csv('console_audit_logs.csv', nrows=100)

print("=" * 60)
print("COLUMNS:")
print(df.columns.tolist())
print("\n" + "=" * 60)
print("SAMPLE DATA (First 5 rows):")
print(df.head())

print("\n" + "=" * 60)
print("UNIQUE EVENT TYPES:")
if 'Event Type' in df.columns:
    print(df['Event Type'].unique())
elif 'event_type' in df.columns:
    print(df['event_type'].unique())
    
print("\n" + "=" * 60)
print("GMAIL EVENTS (if any):")
gmail_df = df[df.apply(lambda row: 'gmail' in str(row).lower() or 'mail' in str(row).lower(), axis=1)]
print(f"Found {len(gmail_df)} Gmail-related rows")
if len(gmail_df) > 0:
    print(gmail_df.head(10))
