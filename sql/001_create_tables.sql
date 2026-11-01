-- 001_create_tables.sql
-- Tabelas do pipeline de Atas de Registro de Precos (PNCP).
-- Pode ser executado varias vezes: o IF NOT EXISTS evita erro se a tabela ja existe.


-- 1) STAGING: guarda cada ata exatamente como chegou da API (caixa de entrada).
CREATE TABLE IF NOT EXISTS staging_atas (
    numero_controle_pncp_ata TEXT        NOT NULL,  -- codigo unico da ata
    data_inicial_consulta    DATE        NOT NULL,  -- periodo que foi consultado
    data_final_consulta      DATE        NOT NULL,
    pagina                   INTEGER     NOT NULL,  -- de qual pagina da API veio
    payload                  JSONB       NOT NULL,  -- a ata original, sem alteracoes
    data_coleta              TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- reexecutar o mesmo periodo atualiza a linha em vez de duplicar
    PRIMARY KEY (numero_controle_pncp_ata, data_inicial_consulta, data_final_consulta)
);


-- 2) ATAS: dados tratados, uma linha por ata (planilha final).
CREATE TABLE IF NOT EXISTS atas (
    numero_controle_pncp_ata    TEXT PRIMARY KEY,   -- chave: codigo unico da ata
    numero_ata_registro_preco   TEXT,
    ano_ata                     INTEGER,
    numero_controle_pncp_compra TEXT NOT NULL,      -- compra que originou a ata
    cancelado                   BOOLEAN NOT NULL DEFAULT FALSE,
    data_cancelamento           DATE,
    data_assinatura             DATE,
    vigencia_inicio             DATE,
    vigencia_fim                DATE,
    data_publicacao_pncp        DATE,
    data_atualizacao_pncp       DATE,               -- ultima atualizacao na fonte
    cnpj_orgao                  TEXT NOT NULL,
    nome_orgao                  TEXT,
    codigo_unidade_orgao        TEXT,
    nome_unidade_orgao          TEXT,
    objeto_contratacao          TEXT,
    possibilidade_adesao        BOOLEAN,            -- pode ser nulo: a fonte nem sempre informa
    primeira_coleta_em          TIMESTAMPTZ NOT NULL DEFAULT now(),  -- quando vimos a ata pela 1a vez
    atualizado_em               TIMESTAMPTZ NOT NULL DEFAULT now(),  -- ultima vez que o nosso registro mudou
    -- regra de integridade: a vigencia nao pode terminar antes de comecar
    CONSTRAINT ck_vigencia CHECK (
        vigencia_fim IS NULL OR vigencia_inicio IS NULL OR vigencia_fim >= vigencia_inicio
    )
);

-- indices para as consultas mais comuns
CREATE INDEX IF NOT EXISTS idx_atas_cnpj_orgao ON atas (cnpj_orgao);
CREATE INDEX IF NOT EXISTS idx_atas_compra     ON atas (numero_controle_pncp_compra);


-- 3) ATAS_REJEITADAS: registros com problema, guardados com o motivo.
CREATE TABLE IF NOT EXISTS atas_rejeitadas (
    id                       BIGSERIAL PRIMARY KEY,
    -- "impressao digital" do registro rejeitado (ata + motivo + conteudo).
    -- Serve para nao guardar o mesmo problema duas vezes se o robo rodar de novo.
    chave_dedup              TEXT        NOT NULL UNIQUE,
    numero_controle_pncp_ata TEXT,                  -- pode faltar, se o problema for justamente esse
    motivo                   TEXT        NOT NULL,  -- por que foi rejeitada
    payload                  JSONB       NOT NULL,  -- o registro original
    data_inicial_consulta    DATE,
    data_final_consulta      DATE,
    data_coleta              TIMESTAMPTZ NOT NULL DEFAULT now()
);
