# Pvragon Activity Tracker - Project Blueprint

## 1. Project Overview
The **Pvragon Activity Tracker** is an enterprise-grade automated reporting system designed to provide high-transparency insights into team productivity across multiple digital platforms. It consolidates activity logs from ClickUp, GitHub, Figma, Google Workspace, and Backendless into a unified audit trail and visual dashboard.

## 2. System Architecture (3-Layer Model)
The project is built on a robust 3-layer architecture to ensure deterministic results and easy maintenance:

1.  **Directive Layer (Strategy):** Context-rich SOPs (Standard Operating Procedures) in Markdown that define "how" each platform should be audited.
2.  **Orchestration Layer (Management):** A central coordinator (`run_daily_workflow.py`) that manages task sequencing, error handling, and data synchronization.
3.  **Execution Layer (Logic):** Specialized Python scripts that perform high-speed API data fetching, normalization, and statistical analysis.

## 3. Data Flow & Ecosystem
Data is processed through a sequential pipeline every 24 hours:
1.  **Ingestion:** Raw event logs are pulled from 5 platform APIs.
2.  **Standardization:** Names and emails are mapped to a canonical "Team Member" list using fuzzy matching and exclusion rules.
3.  **Audit Generation:** A unified matrix is built in the `Daily Audit` tab, filling gaps with zeros to ensure data integrity.
4.  **Time Analysis:** Work sessions are identified using a session-gap model to calculate "Active Window" hours (PST timezone).
5.  **Visualization:** Data is exported to a JSON-based dashboard and summarized via professional email insights.

## 4. Reporting Standards & Timezones
*   **Primary Timezone:** America/Los_Angeles (PST/PDT).
*   **Audit Window:** Rolling 24-hour cycle (12:00 AM - 11:59 PM PST).
*   **Update Schedule:** Daily at 04:00 AM PST (ensuring full data capture for the previous day).

## 5. Platform-Specific Logic

| Platform | Key Events Tracked | Timezone Logic | Accuracy Note |
| :--- | :--- | :--- | :--- |
| **ClickUp** | Tasks, Comments, Chat Messages | UTC → PST | High accuracy for comms; attribution for task updates is best-effort. |
| **GitHub** | Push, Pull Requests, Reviews | UTC → PST | 100% precision for code deployments. |
| **Figma** | File edits, Versioning | UTC → PST | Focused on document contribution cycles. |
| **Google Workspace** | Drive Edits, Sent Emails, Meets | ISO → PST | Precision focus on document creation and active email sends. |
| **Backendless** | Console management, API usage | UTC → PST | 100% audit trail for system administration. |

## 6. Glossary of Tabs
*   **Daily Audit:** The master matrix of every person vs every event for every day.
*   **Activity Time Analysis:** The calculated "Work Day" metrics (Start, End, Active Hours, Longest Break).
*   **Event Type References:** A living glossary defining what each individual activity means.
*   **System Architecture:** (New) Technical metadata and logic definitions for system transparency.

## 7. Visual Dashboard Intelligence
The interactive dashboard (`index.html`) translates raw data into 4 key visual modules:

### A. Team Productivity Trend (Line Chart)
*   **Purpose:** Monitors daily activity volume over time.
*   **Insight:** Identifies "Crunch" periods or unexpected drops in engagement across the core team.

### B. Platform Distribution (Doughnut Chart)
*   **Purpose:** Shows where the team spends most of their digital time.
*   **Insight:** Highlights tool-saturation (e.g., if Figma activity outweighs GitHub, the project is likely in a Design phase).

### C. Leaderboard & Activity Mix (Stacked Bar Chart)
*   **Purpose:** Ranks members by total events, segmented by activity type.
*   **Insight:** Identifies the "Top Contributor" and reveals their specific focus (e.g., heavy coding vs. heavy coordination).

### D. Activity Window (Floating Bar Chart)
*   **Purpose:** Visualizes the start/end times and average duration of each person's workday.
*   **Insight:** Shows team availability and scheduling alignment across timezones. Sorted by duration (descending) to highlight the most active members.

## 8. Summary & Value Proposition
This system represents a shift from "Trust-based" management to **"Data-backed" Transparency**. By automating the audit trail, we remove the burden of manual reporting from the team while providing stakeholders with a real-time, accurate window into the project's pulse.
