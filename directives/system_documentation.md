# System Documentation & Logic Mapping

## Goal
Maintain a high-level "Project Blueprint" and a "System Architecture" metadata tab in the Google Sheet to ensure absolute transparency of the reporting logic.

## Inputs
- `PROJECT_BLUEPRINT.md` (Root)
- `generate_reports.py` (Execution)

## Execution Process (Automated)
1. The script `generate_reports.py` maintains the `System Architecture` tab.
2. It defines:
   - **Tab Names:** Every tab present in the sheet.
   - **Data Sources:** Which API provides the data.
   - **Timezone:** Standardized to America/Los_Angeles (PST).
   - **Update Frequency:** Daily at 04:00 AM PST.
   - **Accuracy Rating:** Confidence score (High/Perfect).
   - **Scope Logic:** Brief explanation of what is included/excluded (e.g., "Sends only").

## Documentation Standards
- **English Only:** All technical documentation and sheet metadata MUST be in English for professional consistency.
- **Visual Evidence:** When possible, document the purpose of specific charts in the dashboard.
- **Persistence:** Ensure that updates to the metadata tabs do not overwrite manual "Description" fields added by the team (applies to `Event Type References`).

## Key Terms
- **Deep Work:** Productivity-focused output (GitHub, Figma, Backendless).
- **Coordination:** Communication-focused output (ClickUp Chats, Gmail).
- **Audit Window:** The 24-hour cycle being reported.

## Maintenance
- This directive should be updated whenever a new data source or reporting logic (e.g., productivity scoring) is implemented.
