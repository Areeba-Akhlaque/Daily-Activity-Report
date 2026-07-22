"""Render the daily email to a local HTML file WITHOUT sending it.

Builds the real thing: same live Sheet data, same chart builder, same HTML
generator the 4 AM run uses — it just stops short of SMTP. Use it to check
wording, colours or layout before a change goes out to managers.

    python orchestration/preview_email.py
    python orchestration/preview_email.py --date=07/20/26
    python orchestration/preview_email.py --out=D:/tmp/email.html

Notes
-----
* The chart is embedded as a QuickChart render URL, so event colours live inside
  the chart IMAGE, not in the HTML. Grepping the HTML for palette hex codes
  returns nothing and that is expected — open the page to see them.
* Charts are built with creds=None so nothing is uploaded to Drive, but the
  builder still writes the PNGs into dashboard/charts/. Those are committed
  artefacts, so run `git checkout -- dashboard/charts/` afterwards if you do not
  want the local copies changed.
"""
import os
import sys
import io

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)


def load_env():
    env_path = os.path.join(ROOT_DIR, '.env')
    if os.path.exists(env_path):
        with open(env_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ[k] = v


def main():
    load_env()
    os.chdir(ROOT_DIR)
    sys.path.insert(0, SCRIPT_DIR)
    sys.path.insert(0, os.path.join(ROOT_DIR, 'execution'))

    target_date = None
    out_path = os.path.join(ROOT_DIR, 'email_preview.html')
    for arg in sys.argv[1:]:
        if arg.startswith('--date='):
            target_date = arg.split('=', 1)[1]
        elif arg.startswith('--out='):
            out_path = arg.split('=', 1)[1]

    import send_daily_email as S

    print('[1/3] Fetching live summary from the Sheet...')
    summary = S.get_daily_summary(S.get_credentials(), target_date_override=target_date)
    print(f"      date={summary['date']}  activities={summary['total_activities']:,}  "
          f"members={summary['active_members']}")

    print('[2/3] Building both charts (creds=None -> no Drive upload)...')
    chart = S.generate_stacked_bar_chart(summary, creds=None)
    work = None
    if summary.get('work_only'):
        work = S.generate_stacked_bar_chart(
            summary['work_only'], creds=None,
            chart_label_suffix=' (Comms Excluded)',
            save_filename_suffix='_work_only')
    print(f'      chart      : {chart}')
    print(f'      work chart : {work}')

    print('[3/3] Rendering email HTML...')
    html = S.generate_email_html(summary, chart, work_chart_url=work)
    with io.open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'\nWROTE {out_path}  ({len(html):,} bytes)')
    print('Open it in a browser. Nothing was emailed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
