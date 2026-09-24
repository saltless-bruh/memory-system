-- Chứng minh phân quyền department được cưỡng chế trong SQL, không phải lọc sau.
--
--   for d in infra redteam ai_eng ''; do
--     docker compose exec -T postgres psql -U postgres -d snp_rag -v dept="$d" \
--       -f - < scripts/demo/rls_scope.sql
--   done
--
-- `set local role rag_app_role` hạ quyền xuống đúng role mà Scout dùng. Role đó
-- là NOBYPASSRLS nên không có đường vòng nào, kể cả từ psql. Department rỗng
-- phải ra 0 -- đó là fail-closed: không khai báo quyền thì không thấy gì.
BEGIN;
SET LOCAL ROLE rag_app_role;
SET LOCAL scout.current_depts = :'dept';

SELECT coalesce(nullif(:'dept', ''), '(rong)')                  AS dept,
       count(*)                                                 AS tai_lieu_nhin_thay,
       count(*) FILTER (WHERE source_uri LIKE 'raw/rehearsal%')  AS chi_infra,
       count(*) FILTER (WHERE source_uri LIKE 'raw/papers%')     AS chi_aieng_blueteam
FROM rag_documents;
COMMIT;
