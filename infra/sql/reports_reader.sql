-- Read-only identity for the Contoso daily payments report (VM dra-vm-reports).
-- Run against paymentsdb on the PRIMARY (connect through the failover group listener
-- <fog>.database.windows.net, Initial Catalog=paymentsdb) as the SQL admin.
-- infra/scripts/reports-vm.sh setup runs it for you (through the VM, password injected at run time);
-- to run it by hand, replace the password placeholder below first and never commit the real value.
--
-- Why a CONTAINED database user (WITH PASSWORD) and not a server login + user:
--   * A contained user, its password hash and its SID live inside paymentsdb itself
--     (sys.database_principals, authentication_type = DATABASE). Active geo-replication / the failover group
--     copies the whole database, so the user exists on the geo-secondary (dra-sql-sea-*) immediately and keeps
--     working after a failover, with the same password. Nothing to recreate in Region B.
--   * A server login lives in the logical server's master database, which is NOT geo-replicated: after a
--     failover the user in paymentsdb would be orphaned unless a login with the same SID had been created on
--     the partner server by hand.
--   * Azure SQL Database supports contained users natively (no 'contained database authentication' setting).
--   * Clients must name the database (Initial Catalog=paymentsdb): a contained user cannot log in to master.
--   * Users can only be created/changed on the primary (the geo-secondary is read-only); the change then
--     replicates within seconds.
--
-- Idempotent: creates the user, or resets its password if it already exists (keeps Key Vault in sync).
-- Single batch (no GO) so it can be sent with one SqlCommand.

IF OBJECT_ID(N'dbo.transactions', N'U') IS NULL
    THROW 50000, 'dbo.transactions does not exist yet: start the payments app once so it creates its tables', 1;

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'reports_reader')
    CREATE USER [reports_reader] WITH PASSWORD = N'__READER_PASSWORD__', DEFAULT_SCHEMA = [dbo];
ELSE
    ALTER USER [reports_reader] WITH PASSWORD = N'__READER_PASSWORD__';

-- Least privilege: SELECT on the one table the report needs, and no role memberships.
GRANT SELECT ON OBJECT::dbo.transactions TO [reports_reader];

DECLARE @role sysname, @stmt nvarchar(400);
DECLARE roles CURSOR LOCAL FAST_FORWARD FOR
    SELECT r.name FROM sys.database_role_members m
    JOIN sys.database_principals r ON r.principal_id = m.role_principal_id
    WHERE m.member_principal_id = DATABASE_PRINCIPAL_ID(N'reports_reader');
OPEN roles;
FETCH NEXT FROM roles INTO @role;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @stmt = N'ALTER ROLE ' + QUOTENAME(@role) + N' DROP MEMBER [reports_reader]';
    EXEC sp_executesql @stmt;
    FETCH NEXT FROM roles INTO @role;
END;
CLOSE roles;
DEALLOCATE roles;

-- What the user can do now (expected: GRANT CONNECT on DATABASE, GRANT SELECT on dbo.transactions)
SELECT p.state_desc, p.permission_name,
       CASE p.class WHEN 0 THEN N'DATABASE'
            ELSE OBJECT_SCHEMA_NAME(p.major_id) + N'.' + OBJECT_NAME(p.major_id) END AS on_object
FROM sys.database_permissions p
WHERE p.grantee_principal_id = DATABASE_PRINCIPAL_ID(N'reports_reader')
ORDER BY p.class, p.permission_name;
