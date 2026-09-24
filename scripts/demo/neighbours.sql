-- Similarity graph, một nút: các trang gần nhất với :page trong không gian vector.
--
-- Dùng để trả lời "cho tôi xem RAG trước và sau khi sync": chạy TRƯỚC khi push
-- (trang chưa có trong index -> 0 dòng) và chạy LẠI sau khi push.
--
--   docker compose exec -T postgres psql -U postgres -d snp_rag \
--     -v page='concepts/ten-trang.md' -f - < scripts/demo/neighbours.sql
--
-- Cosine ở đây là cùng phép đo mà wiki_search dùng cho dense arm; khác biệt duy
-- nhất là wiki_search còn hợp nhất thêm lexical arm bằng RRF và lọc theo RLS.
WITH me AS (
    SELECT c.embedding
    FROM rag_chunks c
    JOIN rag_documents d USING (doc_id)
    WHERE d.source_uri LIKE '%' || :'page'
    ORDER BY c.chunk_index
    LIMIT 1
)
SELECT round((1 - (x.embedding <=> me.embedding))::numeric, 3) AS cosine,
       x.source_uri
FROM (
    SELECT DISTINCT ON (d.doc_id) d.doc_id, d.source_uri, c.embedding
    FROM rag_chunks c
    JOIN rag_documents d USING (doc_id), me
    WHERE d.source_uri NOT LIKE '%' || :'page'
    ORDER BY d.doc_id, c.embedding <=> me.embedding
) x, me
ORDER BY x.embedding <=> me.embedding
LIMIT 10;
