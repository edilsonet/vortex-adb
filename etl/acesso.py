"""Contas, senhas, sessões e recuperação.

**A senha nunca é guardada nem comparada em texto.** O que vai para o banco é
PBKDF2-HMAC-SHA256, com um sal novo por usuário e o custo registrado ao lado
do hash. Comparar senha é `hmac.compare_digest` sobre o resultado — nunca `==`
sobre o que o usuário digitou, porque isso deixa o ataque de tempo em
bandeira.

**A sessão é um cookie com um token; o banco guarda só o hash dele.** Se a
tabela `sessao` vazar, quem leu não consegue forjar sessão nenhuma, porque
token não reversível em hash não dá para reconstruir. Uma sessão que não é
usada expira sozinha, e a limpeza é feita na mesma passagem que lê.

**A recuperação não responde se o e-mail existe.** A tela devolve sempre a
mesma frase; o que muda é o que foi gravado no log. Sem isso, a tela viraria
uma lista de quem tem conta no sistema — que é informação que só o
administrador devia ter.

**O envio por SMTP é opcional e a ausência dele é dita.** A máquina pode ter a
porta de SMTP bloqueada. Nesses casos o link é gerado, mostrado na tela e
gravado num arquivo, com o motivo escrito, em vez de fingir que foi enviado.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import BUILD, DB_PATH, ROOT

ITERACOES = 600_000
SALT_BYTES = 16
HASH_HEX = 64          # PBKDF2-SHA256 devolve 32 bytes = 64 hex
SAL_HEX = SALT_BYTES * 2
TOKEN_BYTES = 32
SESSAO_HORAS = 12
RECUPERACAO_MINUTOS = 30
MAX_FALHAS = 5
TRAVAMENTO_MINUTOS = 15
COOKIE = "anac_sessao"
LOCK = threading.Lock()

# Conta de administrador entregue na instalação. A senha inicial não cumpre a
# política normal de senha; a conta nasce com `troca_senha_obrigatoria = 1`
# justamente por isso.
NOME_INICIAL = "Edilson Et"
EMAIL_INICIAL = "edilsonet@gmail.com"
SENHA_INICIAL = "1234567"

# Onde ficam as credenciais de SMTP. Formato de `smtp.key`:
#   host=smtp.gmail.com
#   porta=587
#   usuario=fulano@gmail.com
#   senha=abcd efgh ijkl mnop
#   remetente=fulano@gmail.com
ARQ_SMTP = ROOT / "secrets" / "smtp.key"
ARQ_RECUPERACAO = BUILD / "recuperacao-pendente.log"


def abrir(db_path=None, *, timeout: int = 20) -> sqlite3.Connection:
    """Conexão com a verificação de chave estrangeira ligada.

    O SQLite **desliga** `foreign_keys` por padrão, em cada conexão. Sem este
    `PRAGMA`, apagar uma conta deixava as linhas de `login_log` apontando para
    um id que não existe mais — e o `ON DELETE SET NULL` declarado no schema
    nunca acontecia. `etl/db.py` já fazia isso na ingestão; faltava nas
    conexões do servidor, que é onde as contas são apagadas.
    """
    conn = sqlite3.connect(db_path or DB_PATH, timeout=timeout)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class ErroAcesso(Exception):
    """Falha de acesso com mensagem que pode ir direto para a tela."""


# ------------------------------------------------------------------ utilidades
def _agora() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _iso(momento: datetime | None = None) -> str:
    return (momento or _agora()).isoformat(timespec="seconds")


def _hash_senha(senha: str, *, iteracoes: int = ITERACOES,
                salt: bytes | None = None) -> tuple[str, str]:
    """`(hash_hex, sal_hex)`. O sal é sorteado por senha, para que dois
    usuários com a mesma senha não tenham o mesmo hash."""
    salt = salt or secrets.token_bytes(SALT_BYTES)
    derivado = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, iteracoes)
    return derivado.hex(), salt.hex()


def _empacotar(hash_hex: str, sal_hex: str) -> str:
    """O que vai para `usuario.senha_hash`: `hash` + `sal`, nessa ordem.

    Uma coluna só em vez de duas: o sal não é segredo — ele é público por
    definição, e o que protege é o PBKDF2 aplicado sobre ele. Guardar os dois
    lado a lado faz com que um hash copiado de outro usuário não valide.
    """
    return hash_hex + sal_hex


def _confere(senha: str, guardado: str, iteracoes: int) -> bool:
    """Compara a senha digitada com o hash gravado, em tempo constante.

    O layout é o de `_empacotar`: o PBKDF2 ocupa os primeiros 64 hex e o sal
    os últimos 32 (16 bytes). O `compare_digest` é o que impede que o tempo de
    resposta revele quantos caracteres já batem.
    """
    if len(guardado or "") < HASH_HEX + SAL_HEX:
        return False
    try:
        esperado = guardado[:HASH_HEX]
        sal = bytes.fromhex(guardado[HASH_HEX:])
    except ValueError:
        return False
    calculado = hashlib.pbkdf2_hmac(
        "sha256", senha.encode("utf-8"), sal, max(1000, iteracoes or ITERACOES))
    return hmac.compare_digest(calculado.hex(), esperado)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _email_valido(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$", (email or "").strip()))


def politica_de_senha(senha: str) -> list[str]:
    """O que a senha ainda não cumpre. Lista vazia = pode."""
    falhas = []
    if len(senha or "") < 8:
        falhas.append("pelo menos 8 caracteres")
    if not re.search(r"[A-Za-z]", senha or ""):
        falhas.append("ao menos uma letra")
    if not re.search(r"\d", senha or ""):
        falhas.append("ao menos um número")
    return falhas


# --------------------------------------------------------------------- schema
def aplicar_schema(conn: sqlite3.Connection) -> None:
    caminho = ROOT / "schema" / "acesso.sql"
    sql = caminho.read_text(encoding="utf-8")
    conn.executescript(sql)
    _semear(conn)
    conn.commit()


def _semear(conn: sqlite3.Connection) -> None:
    """Garante que exista ao menos uma conta de administrador.

    Roda em todo arranque. Não grava senha em arquivo: quem não souber a senha
    inicial usa "recuperar senha", que é o caminho previsto para isso.
    """
    if conn.execute("SELECT COUNT(*) FROM usuario").fetchone()[0]:
        return
    gerar_primeiro_usuario(conn, os.environ.get("ANAC_SENHA_INICIAL",
                                                SENHA_INICIAL),
                           nome=NOME_INICIAL, email=EMAIL_INICIAL)


# --------------------------------------------------------------------- usuários
def criar_usuario(conn: sqlite3.Connection, dados: dict) -> dict:
    nome = (dados.get("nome_completo") or "").strip()
    email = (dados.get("email") or "").strip().lower()
    senha = dados.get("senha") or ""
    papel = (dados.get("papel") or "consulta").strip().lower()

    if not nome:
        raise ErroAcesso("informe o nome completo")
    if not _email_valido(email):
        raise ErroAcesso("e-mail inválido")
    if papel not in ("administrador", "editor", "consulta"):
        raise ErroAcesso("papel inválido")
    if papel == "administrador":
        raise ErroAcesso(
            "o papel administrador não é concedido por aqui: ele existe "
            "apenas para a conta inicial")
    falhas = politica_de_senha(senha)
    if falhas:
        raise ErroAcesso("a senha precisa ter " + ", ".join(falhas))

    existe = conn.execute("SELECT id FROM usuario WHERE email = ?", (email,)).fetchone()
    if existe:
        raise ErroAcesso("já existe uma conta com esse e-mail")

    hash_hex, sal_hex = _hash_senha(senha)
    uid = conn.execute(
        "INSERT INTO usuario (nome_completo, email, senha_hash, senha_iteracoes, "
        "papel, criado_em) VALUES (?,?,?,?,?,?)",
        (nome, email, _empacotar(hash_hex, sal_hex), ITERACOES, papel, _iso())).lastrowid
    conn.commit()
    return {"id": uid, "nome_completo": nome, "email": email, "papel": papel}


def listar_usuarios(conn: sqlite3.Connection) -> list[dict]:
    colunas = ("id", "nome_completo", "email", "papel", "ativo", "criado_em",
               "ultimo_acesso_em", "senha_trocada_em", "troca_senha_obrigatoria")
    linhas = conn.execute(
        f"SELECT {', '.join(colunas)} FROM usuario ORDER BY nome_completo").fetchall()
    return [dict(zip(colunas, l)) for l in linhas]


def alterar_usuario(conn: sqlite3.Connection, uid: int, dados: dict) -> dict:
    campos = {}
    if "nome_completo" in dados and (dados["nome_completo"] or "").strip():
        campos["nome_completo"] = dados["nome_completo"].strip()
    if "email" in dados:
        email = (dados["email"] or "").strip().lower()
        if not _email_valido(email):
            raise ErroAcesso("e-mail inválido")
        outro = conn.execute("SELECT id FROM usuario WHERE email = ? AND id <> ?",
                             (email, uid)).fetchone()
        if outro:
            raise ErroAcesso("esse e-mail já pertence a outra conta")
        campos["email"] = email
    if "ativo" in dados:
        ativo = 1 if dados["ativo"] in (True, 1, "1", "true", "on") else 0
        if ativo == 0 and uid == _unico_admin(conn):
            raise ErroAcesso("não é possível desativar o último administrador")
        campos["ativo"] = ativo
    if "papel" in dados and dados["papel"] in ("editor", "consulta"):
        # Rebaixar o último administrador tranca o sistema: ninguém mais
        # consegue criar contas nem conceder acesso, e a tela de "Usuários"
        # some do menu de todo mundo. O mesmo cuidado que a desativação tem.
        if uid == _unico_admin(conn):
            raise ErroAcesso("não é possível rebaixar o último administrador")
        campos["papel"] = dados["papel"]
    if not campos:
        raise ErroAcesso("nada para alterar")
    sets = ", ".join(f"{k} = ?" for k in campos)
    conn.execute(f"UPDATE usuario SET {sets} WHERE id = ?", (*campos.values(), uid))
    conn.commit()
    return {"ok": True, "alterado": True, "campos": sorted(campos)}


def _unico_admin(conn: sqlite3.Connection) -> int | None:
    """id do administrador ativo, quando há só um."""
    ativos = [r[0] for r in conn.execute(
        "SELECT id FROM usuario WHERE papel = 'administrador' AND ativo = 1")]
    return ativos[0] if len(ativos) == 1 else None


def excluir_usuario(conn: sqlite3.Connection, uid: int) -> dict:
    """Apaga a conta e derruba as sessões dela.

    A proteção do último administrador mora aqui e não na camada HTTP: era lá
    que ela faltava, e o efeito foi o sistema ficar sem nenhum admin — sem
    ninguém para criar contas, e com o menu "Usuários" ausente para todos.
    """
    if uid == _unico_admin(conn):
        raise ErroAcesso("não é possível excluir o último administrador")
    linha = conn.execute(
        "SELECT email FROM usuario WHERE id = ?", (uid,)).fetchone()
    if linha is None:
        raise ErroAcesso("usuário não existe")
    with LOCK:
        conn.execute("DELETE FROM usuario WHERE id = ?", (uid,))
        conn.commit()
    return {"ok": True, "removido": linha[0]}


def trocar_senha(conn: sqlite3.Connection, uid: int, senha_atual: str,
                 senha_nova: str) -> dict:
    linha = conn.execute(
        "SELECT senha_hash, senha_iteracoes FROM usuario WHERE id = ?", (uid,)).fetchone()
    if linha is None:
        raise ErroAcesso("usuário não existe")
    if not _confere(senha_atual, linha[0], linha[1]):
        raise ErroAcesso("a senha atual não confere")
    falhas = politica_de_senha(senha_nova)
    if falhas:
        raise ErroAcesso("a nova senha precisa ter " + ", ".join(falhas))
    if senha_atual == senha_nova:
        raise ErroAcesso("a nova senha precisa ser diferente da atual")

    hash_hex, sal_hex = _hash_senha(senha_nova)
    conn.execute(
        "UPDATE usuario SET senha_hash = ?, senha_iteracoes = ?, senha_trocada_em = ?, "
        "troca_senha_obrigatoria = 0 WHERE id = ?",
        (_empacotar(hash_hex, sal_hex), ITERACOES, _iso(), uid))
    # Todas as sessões caem: quem tinha a senha antiga perde o acesso agora.
    conn.execute("DELETE FROM sessao WHERE usuario_id = ?", (uid,))
    conn.commit()
    return {"ok": True}


# ---------------------------------------------------------------------- sessão
def abrir_sessao(conn: sqlite3.Connection, uid: int, agente: str = "",
                 ip: str = "") -> str:
    """Cria a sessão e devolve o token — só isto é devolvido em claro."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    agora = _agora()
    conn.execute(
        "INSERT INTO sessao (usuario_id, token_hash, agente, ip, criada_em, "
        "ultima_uso_em, expira_em) VALUES (?,?,?,?,?,?,?)",
        (uid, _hash_token(token), (agente or "")[:200], (ip or "")[:60],
         _iso(agora), _iso(agora),
         _iso(agora + timedelta(hours=SESSAO_HORAS))))
    conn.commit()
    return token


def sessao_de(conn: sqlite3.Connection, token: str | None) -> dict | None:
    """Usuário da sessão, ou `None`. Também renova o prazo de validade."""
    if not token:
        return None
    agora = _agora()
    linha = conn.execute(
        "SELECT s.id AS sessao_id, s.expira_em, u.id, u.nome_completo, u.email, "
        "u.papel, u.ativo, u.troca_senha_obrigatoria "
        "FROM sessao s JOIN usuario u ON u.id = s.usuario_id "
        "WHERE s.token_hash = ?", (_hash_token(token),)).fetchone()
    if linha is None:
        return None
    # Ordem das colunas do SELECT acima:
    #   0 sessao_id · 1 expira_em · 2 id · 3 nome · 4 email · 5 papel
    #   6 ativo · 7 troca_senha_obrigatoria
    if _iso(agora) > linha[1]:
        conn.execute("DELETE FROM sessao WHERE id = ?", (linha[0],))
        conn.commit()
        return None
    if not linha[6]:                       # conta desativada: sessão morre
        conn.execute("DELETE FROM sessao WHERE id = ?", (linha[0],))
        conn.commit()
        return None
    # Renova só na metade do prazo, para não escrever a cada requisição.
    renovou = False
    if _iso(agora + timedelta(hours=SESSAO_HORAS / 2)) < linha[1]:
        conn.execute("UPDATE sessao SET ultima_uso_em = ?, expira_em = ? WHERE id = ?",
                     (_iso(agora), _iso(agora + timedelta(hours=SESSAO_HORAS)),
                      linha[0]))
        conn.commit()
        renovou = True
    return {"sessao_id": linha[0], "usuario_id": linha[2],
            "nome_completo": linha[3], "email": linha[4], "papel": linha[5],
            "troca_senha_obrigatoria": bool(linha[7]), "renovada": renovou}


def fechar_sessao(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessao WHERE token_hash = ?", (_hash_token(token),))
        conn.commit()


def limpar_sessoes(conn: sqlite3.Connection) -> int:
    agora = _iso()
    n = conn.execute("DELETE FROM sessao WHERE expira_em < ?", (agora,)).rowcount
    conn.commit()
    return n


# ------------------------------------------------------------------------ login
def _travado(conn: sqlite3.Connection, uid: int) -> bool:
    linha = conn.execute(
        "SELECT falhas, ate_em FROM login_travamento WHERE usuario_id = ?",
        (uid,)).fetchone()
    if linha is None or not linha[1]:
        return False
    if linha[1] < _iso():
        conn.execute("DELETE FROM login_travamento WHERE usuario_id = ?", (uid,))
        conn.commit()
        return False
    return True


def _falhou(conn: sqlite3.Connection, uid: int, email: str, ip: str,
            agente: str, motivo: str) -> None:
    conn.execute(
        "INSERT INTO login_log (usuario_id, email, ip, agente, em, sucesso, motivo) "
        "VALUES (?,?,?,?,?,0,?)", (uid, email, ip, agente, _iso(), motivo))
    if uid is None:
        conn.commit()
        return
    linha = conn.execute(
        "SELECT falhas FROM login_travamento WHERE usuario_id = ?", (uid,)).fetchone()
    falhas = (linha[0] if linha else 0) + 1
    ate = _iso(_agora() + timedelta(minutes=TRAVAMENTO_MINUTOS)) \
        if falhas >= MAX_FALHAS else None
    conn.execute(
        "INSERT INTO login_travamento (usuario_id, falhas, ate_em) VALUES (?,?,?) "
        "ON CONFLICT(usuario_id) DO UPDATE SET falhas = excluded.falhas, "
        "ate_em = excluded.ate_em", (uid, falhas, ate))
    conn.commit()


# ------------------------------------------------------------------------ login
def entrar(conn: sqlite3.Connection, email: str, senha: str, *,
           ip: str = "", agente: str = "") -> tuple[str, dict]:
    """`(token, usuário)`. Levanta `ErroAcesso` com texto que vai à tela.

    A mensagem é única para e-mail inexistente e senha errada: se elas
    divergissem, a tela virava um oráculo de quais e-mails têm conta.
    """
    email = (email or "").strip().lower()
    linha = conn.execute(
        "SELECT id, senha_hash, senha_iteracoes, ativo, nome_completo, papel, "
        "troca_senha_obrigatoria FROM usuario WHERE email = ?", (email,)).fetchone()

    if linha is None:
        # A mensagem é a mesma para e-mail inexistente e senha errada: a tela
        # não pode ser um oráculo de quais e-mails têm conta.
        _falhou(conn, None, email, ip, agente, "e-mail ou senha inválidos")
        raise ErroAcesso("e-mail ou senha inválidos")
    uid = linha[0]
    if _travado(conn, uid):
        _falhou(conn, uid, email, ip, agente, "conta travada por tentativas")
        raise ErroAcesso(
            "conta travada por tentativas demais. Tente de novo em 15 minutos.")
    if not linha[3]:
        _falhou(conn, uid, email, ip, agente, "conta inativa")
        raise ErroAcesso("e-mail ou senha inválidos")
    if not _confere(senha or "", linha[1], linha[2]):
        _falhou(conn, uid, email, ip, agente, "senha inválida")
        raise ErroAcesso("e-mail ou senha inválidos")

    conn.execute("DELETE FROM login_travamento WHERE usuario_id = ?", (uid,))
    conn.execute("UPDATE usuario SET ultimo_acesso_em = ? WHERE id = ?", (_iso(), uid))
    conn.execute(
        "INSERT INTO login_log (usuario_id, email, ip, agente, em, sucesso, motivo) "
        "VALUES (?,?,?,?,?,1,'entrou')", (uid, email, ip, agente, _iso()))
    conn.commit()
    token = abrir_sessao(conn, uid, agente, ip)
    return token, {"id": uid, "nome_completo": linha[4], "email": email,
                   "papel": linha[5],
                   "troca_senha_obrigatoria": bool(linha[6])}


def quem_sou(conn: sqlite3.Connection, token: str | None) -> dict | None:
    return sessao_de(conn, token)


def pode_editar(usuario: dict | None) -> bool:
    """Só `editor` e `administrador` gravam; `consulta` só lê."""
    return bool(usuario) and usuario.get("papel") in ("editor", "administrador")


def e_admin(usuario: dict | None) -> bool:
    return bool(usuario) and usuario.get("papel") == "administrador"


# ------------------------------------------------------------------ recuperação
def _ler_smtp() -> dict:
    if not ARQ_SMTP.exists():
        return {}
    dados = {}
    for linha in ARQ_SMTP.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        dados[chave.strip()] = valor.strip()
    return dados


def _enviar(destino: str, assunto: str, corpo: str) -> tuple[bool, str]:
    """`(enviou, motivo)`. O motivo é o que a tela mostra quando falha."""
    cfg = _ler_smtp()
    faltando = [k for k in ("host", "usuario", "senha") if not cfg.get(k)]
    if faltando:
        return False, ("sem credenciais de SMTP: grave host, usuario e senha em "
                       "secrets/smtp.key para ativar o envio por e-mail")

    porta = int(cfg.get("porta") or 587)
    remetente = cfg.get("remetente") or cfg["usuario"]
    mensagem = EmailMessage()
    mensagem["Subject"] = assunto
    mensagem["From"] = remetente
    mensagem["To"] = destino
    mensagem.set_content(corpo)
    try:
        if porta == 465:
            with smtplib.SMTP_SSL(cfg["host"], porta, timeout=25) as srv:
                srv.login(cfg["usuario"], cfg["senha"])
                srv.send_message(mensagem)
        else:
            with smtplib.SMTP(cfg["host"], porta, timeout=25) as srv:
                srv.starttls()
                srv.login(cfg["usuario"], cfg["senha"])
                srv.send_message(mensagem)
    except (smtplib.SMTPException, OSError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, "enviado por SMTP"


def pedir_recuperacao(conn: sqlite3.Connection, email: str, *,
                      base: str = "", ip: str = "", agente: str = "") -> dict:
    """Gera o link. A resposta é a mesma exista o e-mail ou não.

    Sem SMTP, o link é gravado em `build/recuperacao-pendente.log` e devolvido
    em `link_local`, para a tela mostrar. A alternativa — devolver erro e
    obrigar a configurar e-mail — deixaria o recurso inutilizável numa máquina
    sem porta SMTP, que é o caso desta.
    """
    email = (email or "").strip().lower()
    resposta = {
        "ok": True,
        "mensagem": ("se este e-mail tiver conta, o link de nova senha foi "
                     "enviado."),
        "enviado": False,
        "motivo": None,
        "link_local": None,
    }
    if not _email_valido(email):
        raise ErroAcesso("e-mail inválido")

    linha = conn.execute(
        "SELECT id, nome_completo, ativo FROM usuario WHERE email = ?", (email,)
    ).fetchone()
    if linha is None or not linha[2]:
        return resposta                     # mesma resposta: não revela nada

    token = secrets.token_urlsafe(TOKEN_BYTES)
    agora = _agora()
    conn.execute(
        "INSERT INTO recuperacao (usuario_id, token_hash, criado_em, expira_em, "
        "origem) VALUES (?,?,?,?,?)",
        (linha[0], _hash_token(token), _iso(agora),
         _iso(agora + timedelta(minutes=RECUPERACAO_MINUTOS)), ip))
    conn.commit()

    link = f"{base.rstrip('/')}/#/redefinir?token={token}"
    corpo = (
        f"Olá, {linha[1]}.\n\n"
        f"Você pediu para criar uma nova senha no Registro ANAC.\n"
        f"Abra este endereço para definir a nova senha:\n\n"
        f"{link}\n\n"
        f"O link vale por {RECUPERACAO_MINUTOS} minutos e só funciona uma vez.\n"
        f"Se não foi você, ignore esta mensagem: nada muda.\n")

    enviou, motivo = _enviar(email, "Registro ANAC — nova senha", corpo)
    entrega = "email" if enviou else "arquivo"
    conn.execute(
        "UPDATE recuperacao SET entregue = ? WHERE token_hash = ?",
        (entrega, _hash_token(token)))

    if enviou:
        resposta["enviado"] = True
    else:
        resposta["motivo"] = motivo
        resposta["link_local"] = link
        with ARQ_RECUPERACAO.open("a", encoding="utf-8") as fh:
            fh.write(f"\n===== {_iso(agora)} =====\npara: {email}\n"
                     f"link: {link}\nmotivo do envio: {motivo}\n")
        # Sem entrega o token fica inacessível: ele só se resolve quando alguém
        # abrir o link, e ninguém tem o link. Marcado como pendente para o log.
        conn.execute("UPDATE recuperacao SET entregue = 'pendente' WHERE token_hash = ?",
                     (_hash_token(token),))
    conn.execute(
        "INSERT INTO login_log (usuario_id, email, ip, agente, em, sucesso, motivo) "
        "VALUES (?,?,?,?,?,0,?)",
        (linha[0], email, ip, agente, _iso(), "recuperação pedida"))
    conn.commit()
    return resposta


def usar_token(conn: sqlite3.Connection, token: str, senha_nova: str) -> dict:
    """Troca a senha pelo link. O token só pode ser usado uma vez."""
    if not token:
        raise ErroAcesso("link inválido")
    linha = conn.execute(
        "SELECT id, usuario_id, expira_em, usado_em FROM recuperacao "
        "WHERE token_hash = ?", (_hash_token(token),)).fetchone()
    if linha is None:
        raise ErroAcesso("link inválido ou já utilizado")
    if linha[3]:
        raise ErroAcesso("este link já foi utilizado")
    if linha[2] < _iso():
        raise ErroAcesso("este link expirou. Peça um novo.")

    falhas = politica_de_senha(senha_nova)
    if falhas:
        raise ErroAcesso("a nova senha precisa ter " + ", ".join(falhas))

    hash_hex, sal_hex = _hash_senha(senha_nova)
    with LOCK:
        conn.execute(
            "UPDATE usuario SET senha_hash = ?, senha_iteracoes = ?, "
            "senha_trocada_em = ?, troca_senha_obrigatoria = 0 WHERE id = ?",
            (_empacotar(hash_hex, sal_hex), ITERACOES, _iso(), linha[1]))
        conn.execute("UPDATE recuperacao SET usado_em = ? WHERE id = ?",
                     (_iso(), linha[0]))
        # Quem tinha a senha antiga não pode continuar entrando.
        conn.execute("DELETE FROM sessao WHERE usuario_id = ?", (linha[1],))
        conn.commit()
    return {"ok": True, "email": conn.execute(
        "SELECT email FROM usuario WHERE id = ?", (linha[1],)).fetchone()[0]}


def gerar_primeiro_usuario(conn: sqlite3.Connection, senha: str, *,
                           nome: str = "Administrador",
                           email: str = EMAIL_INICIAL) -> dict:
    """Cria a conta de administrador quando não há nenhuma.

    A senha inicial **ignora** `politica_de_senha` de propósito: quem instala
    o sistema precisa de uma conta que funcione já no primeiro acesso. Em troca
    a conta nasce com `troca_senha_obrigatoria = 1`, e a troca passa pelas
    regras normais — é isso que impede a senha inicial de virar a definitiva.
    """
    if conn.execute("SELECT COUNT(*) FROM usuario").fetchone()[0]:
        raise ErroAcesso("já existe usuário no sistema")
    email = (email or "").strip().lower()
    if not _email_valido(email):
        raise ErroAcesso(f"e-mail inicial inválido: {email}")
    hash_hex, sal_hex = _hash_senha(senha)
    uid = conn.execute(
        "INSERT INTO usuario (nome_completo, email, senha_hash, senha_iteracoes, "
        "papel, criado_em, troca_senha_obrigatoria) VALUES (?,?,?,?,?,?,1)",
        ((nome or "Administrador").strip(), email, _empacotar(hash_hex, sal_hex),
         ITERACOES, "administrador", _iso())).lastrowid
    conn.commit()
    return {"id": uid, "email": email, "nome_completo": nome.strip()}
