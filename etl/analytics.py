"""Consultas analíticas do painel.

Os recortes por entidade usam o snapshot mais recente, não a série inteira: a
série de 12 meses é a dimensão temporal do primeiro bloco, e somá-la às demais
contagens repetiria a mesma aeronave uma vez por mês.
"""

from __future__ import annotations

import sqlite3

MES_ATUAL = "2026-09"


def _q(conn, sql, params=()):
    return [dict(zip([c[0] for c in cur.description], row))
            for cur in [conn.execute(sql, params)] for row in cur.fetchall()]


def _one(conn, sql, params=()):
    r = conn.execute(sql, params).fetchone()
    return r[0] if r else None


# A ANAC usa `Indisponível` no lugar do nome/documento. Sem o filtro, essa
# string entra como se fosse uma empresa e polui o topo de qualquer ranking.
_NAO_PESSOA = "AND s.nome NOT IN ('Indisponível', 'Indisponivel', 'DESCONHECIDO', 'N/I')"


def frota_mensal(conn):
    return _q(conn, "SELECT mes, contagem, esquema_era FROM snapshot ORDER BY mes")


def frota_por_marca(conn, mes=MES_ATUAL, limite=12):
    """Prefixos de matrícula, não marcas comerciais.

    O campo `MARCAS` do RAB é o prefixo de matrícula da aeronave (PPAAA, PSBS),
    não o nome do fabricante. Serve para ver como a frota se distribui nas
    séries de matrícula, mas não descreve quem fabrica.
    """
    return _q(conn, """
        SELECT m.id AS marca_id, COALESCE(a.marca,'(sem marca)') AS marca, COUNT(*) AS n
        FROM aeronave a LEFT JOIN marca m ON m.nome = a.marca
        WHERE a.snapshot_mes = ?
        GROUP BY a.marca ORDER BY n DESC LIMIT ?
    """, (mes, limite))


def frota_por_classe(conn, mes=MES_ATUAL, limite=10):
    """Frota por classe de aeronave (campo `CD_CLS` do RAB)."""
    return _q(conn, """
        SELECT COALESCE(NULLIF(a.cd_classe,''),'(sem classe)') AS classe, COUNT(*) AS n
        FROM aeronave a
        WHERE a.snapshot_mes = ?
        GROUP BY classe ORDER BY n DESC LIMIT ?
    """, (mes, limite))


def frota_por_decada(conn, mes=MES_ATUAL):
    """Frota por década de fabricação — mostra a idade real da frota."""
    return _q(conn, """
        SELECT SUBSTR(CAST(nr_ano_fabricacao AS TEXT), 1, 3) || '0' AS decada,
               COUNT(*) AS n
        FROM aeronave
        WHERE snapshot_mes = ? AND nr_ano_fabricacao GLOB '[0-9][0-9][0-9][0-9]'
        GROUP BY decada ORDER BY decada
    """, (mes,))


def concentracao_propriedade(conn, mes=MES_ATUAL, limite=15):
    """Top proprietários do snapshot atual e a cauda longa da frota.

    O proprietário é quem consta como `PROPRIETARIO` com qualquer participação;
    uma aeronave com 100% de um segurador entra com 1, com 50/50 entra nos dois.
    """
    return _q(conn, f"""
        SELECT s.id AS pessoa_id, s.nome, s.natureza, s.uf,
               COUNT(DISTINCT p.aeronave_id) AS aeronaves,
               ROUND(SUM(COALESCE(p.percentual, 100)) * 1.0 / COUNT(*), 1) AS pct_medio
        FROM participacao p
        JOIN pessoa s ON s.id = p.pessoa_id
        WHERE p.papel = 'PROPRIETARIO' AND p.snapshot_mes = ? {_NAO_PESSOA}
        GROUP BY s.id
        ORDER BY aeronaves DESC LIMIT ?
    """, (mes, limite))


def hhi_propriedade(conn, mes=MES_ATUAL):
    """Índice Herfindahl-Hirschman das participações de propriedade.

    Mede quanto a frota está concentrada: 0 = cada aeronave tem um dono distinto,
    valores próximos de 10.000 = um único dono. Considera apenas vínculos
    com percentual conhecido, porque participação ausente não pode ser
    normalizada sem inventar peso.
    """
    linhas = conn.execute(f"""
        SELECT s.id, SUM(COALESCE(p.percentual, 100)) AS fatia
        FROM participacao p JOIN pessoa s ON s.id = p.pessoa_id
        WHERE p.papel = 'PROPRIETARIO' AND p.snapshot_mes = ? {_NAO_PESSOA}
        GROUP BY s.id
    """, (mes,)).fetchall()
    total = sum(f or 0 for _, f in linhas)
    if not total:
        return {"hhi": 0.0, "participantes": 0, "fatia_total": 0}
    hhi = sum(((f or 0) / total * 100) ** 2 for _, f in linhas)
    return {
        "hhi": round(hhi, 1),
        "participantes": len(linhas),
        "fatia_total": round(total, 1),
        "top1": round(max((f or 0) for _, f in linhas) / total * 100, 2),
    }


def mix_autorizacoes(conn, mes=MES_ATUAL, limite=15):
    """Operadores com e sem operação sob RBAC 121 e RBAC 135.

    Base normativa: RBAC 45.12-I(a) — a aeronave só pode operar sob o RBAC 135
    se estiver inscrita como "TRANSPORTE PÚBLICO". A fonte traz esses flags por
    operador, então a contagem é direta.
    """
    return _q(conn, f"""
        SELECT s.id AS pessoa_id, s.nome, s.natureza, s.uf,
               MAX(CASE WHEN p.operacao_121 = 'S' THEN 1 ELSE 0 END) AS tem_121,
               MAX(CASE WHEN p.operacao_135 = 'S' THEN 1 ELSE 0 END) AS tem_135,
               MAX(CASE WHEN p.transp_reg_121 = 'S' THEN 1 ELSE 0 END) AS reg_121,
               MAX(CASE WHEN p.sae = 'S' THEN 1 ELSE 0 END) AS sae,
               COUNT(DISTINCT p.aeronave_id) AS aeronaves
        FROM participacao p JOIN pessoa s ON s.id = p.pessoa_id
        WHERE p.papel = 'OPERADOR' AND p.snapshot_mes = ? {_NAO_PESSOA}
        GROUP BY s.id
        HAVING tem_121 = 1 OR tem_135 = 1
        ORDER BY tem_121 DESC, tem_135 DESC, aeronaves DESC LIMIT ?
    """, (mes, limite))


def totais_autorizacoes(conn, mes=MES_ATUAL):
    return {
        "121": _one(conn, "SELECT COUNT(DISTINCT pessoa_id) FROM participacao "
                          "WHERE papel='OPERADOR' AND snapshot_mes=? AND operacao_121='S'", (mes,)),
        "135": _one(conn, "SELECT COUNT(DISTINCT pessoa_id) FROM participacao "
                          "WHERE papel='OPERADOR' AND snapshot_mes=? AND operacao_135='S'", (mes,)),
        "com_qualquer_operacao": _one(conn,
            "SELECT COUNT(DISTINCT pessoa_id) FROM participacao "
            "WHERE papel='OPERADOR' AND snapshot_mes=?", (mes,)),
    }


def aerodromos_por_tipo(conn):
    return _q(conn, "SELECT tipo, COUNT(*) AS n FROM aerodromo GROUP BY tipo ORDER BY n DESC")


def aerodromos_por_uf(conn, limite=15):
    return _q(conn, """
        SELECT COALESCE(uf,'(sem UF)') AS uf, COUNT(*) AS n,
               SUM(CASE WHEN tipo='PUBLICO' THEN 1 ELSE 0 END) AS publicos
        FROM aerodromo GROUP BY uf ORDER BY n DESC LIMIT ?
    """, (limite,))


def aerodromos_publicos(conn, limite=12):
    return _q(conn, """
        SELECT icao, nome, municipio, uf, altitude, situacao
        FROM aerodromo WHERE tipo='PUBLICO' AND uf IS NOT NULL
        ORDER BY uf, icao LIMIT ?
    """, (limite,))


def fabricantes_top(conn, limite=12):
    return _q(conn, """
        SELECT f.id AS fabricante_id, s.id AS pessoa_id, s.nome,
               COUNT(DISTINCT a.id) AS aeronaves,
               COUNT(DISTINCT mo.id) AS modelos
        FROM fabricante f
        JOIN pessoa s ON s.id = f.pessoa_id
        LEFT JOIN modelo mo ON mo.fabricante_id = f.id
        LEFT JOIN aeronave a ON a.fabricante_id = f.id
        GROUP BY f.id ORDER BY aeronaves DESC LIMIT ?
    """, (limite,))


def modelos_top(conn, limite=12):
    return _q(conn, """
        SELECT m.id AS modelo_id, m.ds_modelo, mk.nome AS marca,
               s.nome AS fabricante, COUNT(*) AS n
        FROM modelo m
        LEFT JOIN marca mk ON mk.id = m.marca_id
        LEFT JOIN fabricante f ON f.id = m.fabricante_id
        LEFT JOIN pessoa s ON s.id = f.pessoa_id
        JOIN aeronave a ON a.modelo_id = m.id
        GROUP BY m.id ORDER BY n DESC LIMIT ?
    """, (limite,))


def sisant_por_ramo(conn, limite=10):
    return _q(conn, """
        SELECT COALESCE(ramo_atividade,'(não informado)') AS ramo, COUNT(*) AS n
        FROM registro_sisant GROUP BY ramo ORDER BY n DESC LIMIT ?
    """, (limite,))


def sisant_split(conn):
    return _q(conn, """
        SELECT s.natureza, COUNT(DISTINCT r.codigo_aeronave) AS n
        FROM registro_sisant r JOIN pessoa s ON s.id = r.pessoa_id
        GROUP BY s.natureza
    """)


def sisant_fabricantes(conn, limite=10):
    return _q(conn, """
        SELECT COALESCE(fabricante_nome,'(sem fabricante)') AS fabricante,
               COUNT(*) AS n, ROUND(SUM(peso_max_kg), 1) AS peso
        FROM registro_sisant GROUP BY fabricante ORDER BY n DESC LIMIT ?
    """, (limite,))


def rede_participacao(conn, mes=MES_ATUAL, limite=12):
    """Empresas com maior número de vínculos no snapshot atual.

    Mostra a cauda longa: poucas instituições concentram muitas aeronaves e o
    restante são proprietarios de frota pequena.
    """
    return _q(conn, f"""
        SELECT s.id AS pessoa_id, s.nome, s.natureza, p.papel, COUNT(*) AS vinculos,
               COUNT(DISTINCT p.aeronave_id) AS aeronaves
        FROM participacao p JOIN pessoa s ON s.id = p.pessoa_id
        WHERE p.snapshot_mes = ? {_NAO_PESSOA}
        GROUP BY s.id, p.papel
        ORDER BY vinculos DESC LIMIT ?
    """, (mes, limite))


def redemet_status_por_cor(conn):
    """Cores do endpoint de status, conforme a tabela da API.

    g verde, y amarelo, gw verde com aviso, yw amarelo com aviso,
    cinza sem METAR disponível.
    """
    return _q(conn, """
        SELECT COALESCE(cor,'(sem cor)') AS cor, COUNT(*) AS n
        FROM redemet_aerodromo_status GROUP BY cor ORDER BY n DESC
    """)


def redemet_mensagens(conn, tipo, limite=12):
    return _q(conn, f"""
        SELECT icao, validade_inicial, mensagem, recebimento
        FROM redemet_mensagem WHERE tipo = ?
        ORDER BY validade_inicial DESC LIMIT ?
    """, (tipo, limite))


def redemet_resumo(conn):
    return {
        "localidades": _one(conn, "SELECT COUNT(*) FROM redemet_aerodromo_status"),
        "vinculadas": _one(conn, "SELECT COUNT(*) FROM redemet_aerodromo_status "
                                 "WHERE aerodromo_icao IS NOT NULL"),
        "metar": _one(conn, "SELECT COUNT(*) FROM redemet_mensagem WHERE tipo='METAR'"),
        "taf": _one(conn, "SELECT COUNT(*) FROM redemet_mensagem WHERE tipo='TAF'"),
        "requisicoes": _one(conn, "SELECT COALESCE(SUM(requisicoes),0) FROM redemet_coleta"),
        "ultima_coleta": _one(conn, "SELECT MAX(executado_em) FROM redemet_coleta"),
    }


def qualidade(conn, mes=MES_ATUAL):
    total = _one(conn, "SELECT COUNT(*) FROM participacao")
    return {
        "participacoes": total,
        "sem_documento": _one(conn, """
            SELECT COUNT(*) FROM participacao p JOIN pessoa s ON s.id=p.pessoa_id
            WHERE s.documento IS NULL"""),
        "cpf_mascarado": _one(conn, """
            SELECT COUNT(*) FROM participacao p JOIN pessoa s ON s.id=p.pessoa_id
            WHERE s.documento_mascarado=1"""),
        "proprietarios_sem_pct": _one(conn, """
            SELECT COUNT(*) FROM participacao
            WHERE papel='PROPRIETARIO' AND percentual IS NULL"""),
        "aeronave_sem_fabricante": _one(conn, """
            SELECT COUNT(*) FROM aeronave WHERE fabricante_id IS NULL"""),
        "aeronave_sem_modelo": _one(conn, """
            SELECT COUNT(*) FROM aeronave WHERE modelo_id IS NULL"""),
        # Conta referência em qualquer tabela, não só em `participacao`: 127 mil
        # pessoas vêm do SISANT e 2.303 são fabricantes, e nenhuma das duas
        # aparece em `participacao`. Medir só ali acusava 129.560 orfas
        # quando eram 2.
        "pessoas_sem_vinculo": _one(conn, """
            SELECT COUNT(*) FROM pessoa s
            WHERE NOT EXISTS (SELECT 1 FROM participacao p WHERE p.pessoa_id = s.id)
              AND NOT EXISTS (SELECT 1 FROM fabricante f WHERE f.pessoa_id = s.id)
              AND NOT EXISTS (SELECT 1 FROM registro_sisant r WHERE r.pessoa_id = s.id)"""),
        "pessoas_sem_participacao": _one(conn, """
            SELECT COUNT(*) FROM pessoa s WHERE NOT EXISTS
            (SELECT 1 FROM participacao p WHERE p.pessoa_id = s.id)"""),
        "violacoes_fk": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
        "mes": mes,
    }


def resumo_geral(conn, mes=MES_ATUAL):
    return {
        "aeronaves": _one(conn, "SELECT COUNT(*) FROM aeronave WHERE snapshot_mes=?", (mes,)),
        "pessoas": _one(conn, "SELECT COUNT(*) FROM pessoa"),
        "usuarios": _one(conn, "SELECT COUNT(*) FROM v_usuario"),
        "empresas": _one(conn, "SELECT COUNT(*) FROM v_empresa"),
        "fabricantes": _one(conn, "SELECT COUNT(*) FROM fabricante"),
        "marcas": _one(conn, "SELECT COUNT(*) FROM marca"),
        "modelos": _one(conn, "SELECT COUNT(*) FROM modelo"),
        "aerodromos": _one(conn, "SELECT COUNT(*) FROM aerodromo"),
        "sisant": _one(conn, "SELECT COUNT(*) FROM registro_sisant"),
        "produtos": _one(conn, "SELECT COUNT(*) FROM produto_aeronautico"),
        "pecas": _one(conn, "SELECT COUNT(*) FROM peca_aprovada"),
        "participacoes": _one(conn, "SELECT COUNT(*) FROM participacao"),
    }
