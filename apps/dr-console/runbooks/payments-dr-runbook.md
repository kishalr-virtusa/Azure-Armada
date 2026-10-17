# Contoso Payments DR Runbook

**Application:** Contoso Payments  
**Tier:** 1 (Mission Critical)  
**RTO Target:** 30 minutes  
**RPO Target:** 15 minutes  
**Last Updated:** 2026-06-15  
**Owner:** Platform Engineering Team

---

## Overview

This runbook describes the disaster recovery procedure for the Contoso Payments application. The app runs in two Azure regions with SQL geo-replication and Azure Front Door for traffic management.

## Pre-Requisites

- Azure CLI installed and authenticated
- Access to Azure Portal with Contributor role on `rg-contoso-payments`
- VPN connection to Azure (if on-premises)
- Notification channel: Teams "Payments Alerts" channel

---

## DR Procedure

### Step 1: Confirm Outage

1. Check Azure Status page for regional outages
2. Verify the primary region app is not responding:
   ```bash
   curl -s https://fd-payments.azurefd.net/health
   ```
3. Check Application Insights for error spikes
4. Confirm with the on-call engineer

**Success Criteria:** Outage confirmed by at least 2 team members

---

### Step 2: Notify Stakeholders

1. Post in Teams "Payments Alerts" channel
2. Update the status page to "Investigating"
3. Send email to payments-oncall@contoso.com

**Owner:** On-call engineer

---

### Step 3: Check SQL Replication Status

1. Open Azure Portal > `sql-payments-old` > Failover Groups
2. Verify replication state is "Synchronized" or lag is acceptable
3. Note the current primary region

```bash
az sql failover-group show \
  --name fog-payments \
  --server sql-payments-old \
  --resource-group rg-contoso-payments \
  --query "{role:replicationRole, state:replicationState}"
```

**Success Criteria:** Replication lag < 15 minutes (RPO target)

---

### Step 4: Initiate SQL Failover

1. Navigate to the failover group in Azure Portal
2. Click "Failover" to promote the secondary to primary
3. For forced failover (accept data loss): use `--allow-data-loss` flag

```bash
az sql failover-group set-primary \
  --name fog-payments \
  --server sql-payments-secondary \
  --resource-group rg-contoso-payments \
  --allow-data-loss
```

**Owner:** Database Team  
**Success Criteria:** Secondary becomes Primary, replication resumes

---

### Step 5: Update Front Door Routing

1. Open Azure Portal > Front Door profile `fd-payments`
2. Navigate to Origin Groups > payments-origins
3. Disable the primary region origin
4. Verify traffic routes to secondary region

```bash
az afd origin update \
  --resource-group rg-contoso-payments \
  --profile-name fd-payments \
  --origin-group-name payments-origins \
  --origin-name primary-origin \
  --enabled-state Disabled
```

**Success Criteria:** Front Door health probe shows secondary as healthy

---

### Step 6: Verify Application Health

1. Test the application via Front Door:
   ```bash
   curl -s https://fd-payments.azurefd.net/health | jq
   ```
2. Verify the response shows the DR region
3. Execute a test transaction
4. Check Application Insights for successful requests

**Success Criteria:** Health check returns 200, region = DR region, test txn succeeds

---

### Step 7: Update Status Page

1. Change status to "Operational (DR Mode)"
2. Post update in Teams channel
3. Send notification email

**Owner:** On-call engineer

---

### Step 8: Document Recovery Metrics

1. Record the actual RTO (time from outage to recovery)
2. Record the actual RPO (data loss, if any)
3. Compare against targets

---

## Failback Procedure

### Step 9: Confirm Primary Region Recovery

1. Verify the primary region is healthy
2. Check that the original primary database is back online
3. Confirm replication has caught up

---

### Step 10: Initiate Planned Failback

1. Perform a planned failover back to primary region:
   ```bash
   az sql failover-group set-primary \
     --name fog-payments \
     --server sql-payments-primary \
     --resource-group rg-contoso-payments
   ```
2. Wait for replication to synchronize

**Owner:** Database Team

---

### Step 11: Restore Front Door Routing

1. Re-enable the primary region origin
2. Verify traffic returns to primary

```bash
az afd origin update \
  --resource-group rg-contoso-payments \
  --profile-name fd-payments \
  --origin-group-name payments-origins \
  --origin-name primary-origin \
  --enabled-state Enabled
```

---

### Step 12: Post-Incident Review

1. Schedule a post-incident review within 48 hours
2. Document:
   - Root cause
   - Timeline of events
   - Actual RTO/RPO vs targets
   - Improvement items
3. Update this runbook if needed

---

## Contacts

| Role | Name | Phone | Email |
|------|------|-------|-------|
| Primary On-Call | Rotation | +1-555-0100 | payments-oncall@contoso.com |
| Database Team | DB Team | +1-555-0101 | db-team@contoso.com |
| Platform Lead | Platform | +1-555-0102 | platform@contoso.com |

---

## Appendix: Resource Names

| Resource | Name | Region |
|----------|------|--------|
| Primary SQL Server | sql-payments-old | East Asia |
| Secondary SQL Server | sql-payments-secondary | South India |
| Failover Group | fog-payments | - |
| Front Door | fd-payments | Global |
| Primary App | app-payments-cin | East Asia |
| Secondary App | app-payments-sin | South India |
