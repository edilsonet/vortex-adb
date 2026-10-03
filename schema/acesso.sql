-- ============================================================================
--  Acesso: usuários, sessões e recuperação de senha.
--
--  Fica em schema/ e não no ETL porque é uma camada de cima: o `etl/run.py`
--  pode ser reexecutado do zero quantas vezes for preciso, e quem loga no
--  sistema não é dado da ANAC — é o operador. Se morasse em `schema.sql`, o
--  rebuild do ETL apagaria os usuários.
--
--  Senha nunca é gravada. `senha_hash` guarda PBKDF2-HMAC-SHA256 com sal
--  próprio, em hex, e `senha_iteracoes` diz quanto custo foi aplicado — o
--  número ao lado do hash é o que permite aumentar o custo no futuro sem
--  invalidar as senhas que já existem.
-- ============================================================================

CREATE TABLE IF NOT EXISTS usuario (
    id                INTEGER PRIMARY KEY,
    nome_completo     TEXT    NOT NULL,
    email             TEXT    NOT NULL UNIQUE,
    senha_hash        TEXT    NOT NULL,
    senha_iteracoes   INTEGER NOT NULL DEFAULT 600000,
    papel             TEXT    NOT NULL DEFAULT 'consulta'
                            CHECK (papel IN ('administrador', 'editor', 'consulta')),
    ativo             INTEGER NOT NULL DEFAULT 1,
    troca_senha_obrigatoria INTEGER NOT NULL DEFAULT 0,
    criado_em         TEXT    NOT NULL,
    ultimo_acesso_em  TEXT,
    senha_trocada_em  TEXT
);

CREATE INDEX IF NOT EXISTS ix_usuario_email ON usuario (email);

-- Uma sessão por navegador. `token` é o que vai no cookie; o hash é o que
-- fica no banco. Guardar o hash e não o token faz o vazamento do banco
-- inútil: quem roubar a tabela não consegue forjar uma sessão.
CREATE TABLE IF NOT EXISTS sessao (
    id           INTEGER PRIMARY KEY,
    usuario_id   INTEGER NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
    token_hash   TEXT    NOT NULL UNIQUE,
    agente       TEXT,
    ip           TEXT,
    criada_em    TEXT    NOT NULL,
    ultima_uso_em TEXT,
    expira_em    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_sessao_usuario ON sessao (usuario_id);
CREATE INDEX IF NOT EXISTS ix_sessao_expira ON sessao (expira_em);

-- Recuperação de senha. O `token_hash` é o do link; `usado_em` garante uso
-- único. Sem o `usado_em`, um link vazado continuaria valendo para sempre.
CREATE TABLE IF NOT EXISTS recuperacao (
    id           INTEGER PRIMARY KEY,
    usuario_id   INTEGER NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
    token_hash   TEXT    NOT NULL UNIQUE,
    criado_em    TEXT    NOT NULL,
    expira_em    TEXT    NOT NULL,
    usado_em     TEXT,
    origem       TEXT,
    entregue     TEXT    NOT NULL DEFAULT 'pendente'
);

CREATE INDEX IF NOT EXISTS ix_recuperacao_usuario ON recuperacao (usuario_id);

-- Tentativas de login. Serve para a tela avisar "e-mail ou senha inválidos"
-- sem revelar qual dos dois estava certo, e para travar quem insistir.
CREATE TABLE IF NOT EXISTS login_log (
    id        INTEGER PRIMARY KEY,
    usuario_id INTEGER REFERENCES usuario(id) ON DELETE SET NULL,
    email     TEXT,
    ip        TEXT,
    agente    TEXT,
    em        TEXT    NOT NULL,
    sucesso   INTEGER NOT NULL,
    motivo    TEXT
);

CREATE INDEX IF NOT EXISTS ix_login_log_em ON login_log (em);

-- Tentativas falhas seguidas por conta. O travamento é por conta, não por IP:
-- é o que impede um atacante de bloquear o acesso de um usuário legítimo.
CREATE TABLE IF NOT EXISTS login_travamento (
    usuario_id  INTEGER PRIMARY KEY REFERENCES usuario(id) ON DELETE CASCADE,
    falhas      INTEGER NOT NULL DEFAULT 0,
    ate_em      TEXT
);

-- ------------------------------------------------------------- conta inicial
-- A conta de administrador NAO e criada aqui. O PBKDF2 exige um sal por
-- usuario e `senha_iteracoes` precisa acompanhar o hash; escrever um valor
-- fixo no .sql produziria uma senha que ninguem consegue reproduzir.
-- Quem cria e `acesso._semear`, chamado no arranque do servidor, e so
-- quando a tabela esta vazia.
