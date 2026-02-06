const fs = require('fs');
const path = require('path');
const { createClient } = require('backendless-console-sdk');

// Configuration
const LOGIN = process.env.BACKENDLESS_DEV_LOGIN;
const PASSWORD = process.env.BACKENDLESS_DEV_PASSWORD;
const APP_ID = process.env.BACKENDLESS_APP_ID;
const CONSOLE_HOST = process.env.BACKENDLESS_API_URL || 'https://develop.backendless.com';

async function main() {
    console.log("Starting Backendless Console Audit Fetch (Node.js)...");

    if (!LOGIN || !PASSWORD) {
        console.error("❌ ERROR: BACKENDLESS_DEV_LOGIN or BACKENDLESS_DEV_PASSWORD is missing.");
        process.exit(1);
    }

    try {
        console.log(`Connecting to Console Host: ${CONSOLE_HOST}`);
        const client = createClient(CONSOLE_HOST);

        console.log(`Logging in as ${LOGIN}...`);
        const user = await client.user.login(LOGIN, PASSWORD);
        console.log(`✅ Login Successful. User ID: ${user.objectId}`);

        console.log(`Fetching Audit Logs for App ID: ${APP_ID}...`);
        const result = await client.security.loadAuditLogs(APP_ID);

        let logs = [];
        if (Array.isArray(result)) {
            logs = result;
        } else if (result && Array.isArray(result.data)) {
            logs = result.data;
        } else if (result && Array.isArray(result.logs)) {
            logs = result.logs;
        } else {
            console.warn("⚠️  Warning: Unexpected log format:", result);
        }

        console.log(`Fetched ${logs.length} log entries.`);

        if (logs.length > 0) {
            const csvLines = [];
            // Python script expects: developer,event,timestamp
            // timestamp matches 'created' (ms)
            csvLines.push('developer,event,timestamp');

            logs.forEach(log => {
                const timestamp = log.created || log.timestamp || 0;
                const event = (log.action || log.event || 'Unknown').replace(/,/g, ' '); // Escape commas

                let dev = 'Unknown';
                if (log.developer && typeof log.developer === 'string') dev = log.developer;
                else if (log.developer && log.developer.email) dev = log.developer.email;
                else if (log.user_email) dev = log.user_email;
                else if (log.email) dev = log.email;

                csvLines.push(`${dev},${event},${timestamp}`);
            });

            const csvPath = path.join(__dirname, '../console_audit_logs.csv');
            fs.writeFileSync(csvPath, csvLines.join('\n'));
            console.log(`✅ Saved ${logs.length} logs to ${csvPath}`);
        } else {
            console.log("ℹ️  No logs found.");
        }

    } catch (err) {
        console.error("❌ Fatal Error in Node Script:", err);
        // Don't exit 1 to avoid failing the whole workflow, just log error so Python uses fallback?
        // But this IS the fallback.
        process.exit(1);
    }
}

main();
