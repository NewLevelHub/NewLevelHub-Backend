-- Orphan users blocking users.0004_user_company_required_roles (PostgreSQL).
-- Run before migrate if migration raises RuntimeError.

SELECT id, email, role
FROM users
WHERE role IN ('employee', 'company_admin')
  AND company_id IS NULL
ORDER BY id;
