-- 000_airflow_db.sql
-- O Airflow guarda o histórico das execuções num banco só dele.
-- Criamos esse banco separado para não misturar com os dados do projeto (pge_atas).
-- Este script roda sozinho na primeira vez que o Postgres sobe.
CREATE DATABASE airflow;
