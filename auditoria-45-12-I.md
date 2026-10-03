# Auditoria RBAC 45.12-I(a) — operacao_135 x inscrição TRANSPORTE PÚBLICO

Data: 2026-09-30 · Snapshot analisado: **2026-09** (era C — único com os flags)
Base normativa: **RBAC 45.12-I(a)** — "Uma pessoa somente pode operar uma
aeronave em operações segundo o RBAC nº 135 se na aeronave estiver colocada a
inscrição 'TRANSPORTE PÚBLICO'." Texto conferido verbatim no corpus
`cerebro-anac/knowledge/anac-legislacao` (RBAC 45 EMD 04, vigente desde
01/07/2020, sem emenda posterior à ingestão).

## Semântica das colunas

Os flags moram em `participacao` (linha operador–aeronave–mês), papel
`OPERADOR`, e vêm da fonte em `OPERADORESARRAY`/`OPERADORESJSON` (era C):

| coluna do banco | campo na fonte |
|---|---|
| `operacao_121` | `OPERACAO121` |
| `operacao_135` | `OPERACAO135` |
| `transp_reg_121` | `TRANSPREGULAR121` |
| `transp_reg_135` | `TRANSPREGULAR135` |

Nas eras A e B os campos não existem (NULL em todas as linhas) — a auditoria
só é possível de 2026-05 em diante.

## Consulta principal (2026-09)

```sql
SELECT COUNT(*),
       SUM(p.operacao_135='S' AND p.transp_reg_135='S'),
       SUM(p.operacao_135='S' AND p.transp_reg_135='N')
FROM participacao p
WHERE p.papel='OPERADOR' AND p.snapshot_mes='2026-09';
```

| grupo | vínculos | significado |
|---|---|---|
| op135='S' (total) | 1.103 | operação declarada sob RBAC 135 |
| **A** com `transp_reg_135='S'` | **96** | alinhado com a regra |
| **B** com `transp_reg_135='N'` | **1.007** | operação 135 **sem** inscrição TRANSPORTE PÚBLICO |
| ausente | 0 | — |

Contexto 121: 62/62 vínculos com `operacao_121='S'` têm `transp_reg_121='S'`
(alinhamento total). Inverso 135: 0 vínculos com inscrição sem operação.
O desalinhamento é exclusivo do par 135.

## Séries mensais (vínculos)

| mês | A (alinhado) | B (transp=N) |
|---|---|---|
| 2026-05 | 56 | 1.011 |
| 2026-06 | 98 | 981 |
| 2026-07 | 96 | 979 |
| 2026-09 | 96 | 1.007 |

Quem está no grupo A (top): HELISUL TÁXI AÉREO LTDA (42), AZUL CONECTA (27),
ATA AEROTAXI ABAETE (15), RIMA Rio Madeira (8), APUÍ TÁXI AÉREO (4).
Quem está no grupo B (top): OMNI TAXI AEREO S.A (105), LÍDER TÁXI AÉREO (41),
VOARE (34), HERINGER (32), BHS (30), AMBIPAR FLYONE (28), AEROLEO (28).

## Diagnóstico

1. **Não é infração demonstrável, é lacuna de preenchimento na fonte.**
   Táxis aéreos certificados de larga escala (OMNI, LÍDER) aparecem no grupo B
   em todos os meses; se fosse infração real, as aeronaves não voariam. As
   aeronaves dos dois grupos têm o mesmo perfil (`tp_operacao` = PRIVADO em
   ~97% em ambos).
2. **A ANAC está corrigindo o campo em produção**: HELISUL TÁXI AÉREO LTDA
   tem 42/42 aeronaves em `transp_reg_135='S'` desde 2026-06, mas 0/42 em
   2026-05. O salto do grupo A (56 → 98) é exatamente essa correção.
3. **O flag é do vínculo, não da aeronave**: entre as 12 aeronaves com mais de
   um vínculo `operacao_135='S'`, 3 têm `transp_reg_135` divergente entre
   operadores — o cruzamento deve seguir por vínculo (como foi feito), nunca
   deduplicado por aeronave.
4. **Identidade fragmentada**: existem "HELISUL LINHAS AEREAS S.A",
   "HELISUL TAXI AEREO LTDA" e "HELISUL TÁXI AÉREO LTDA" como pessoas
   distintas (CPF/CNPJ mascarado ou ausente na fonte) — qualquer leitura por
   operador precisa desambiguar.

## Conclusão normativa

Em 2026-09, 1.007 de 1.103 vínculos com operação declarada sob o RBAC 135
(91,3%) não trazem a inscrição TRANSPORTE PÚBLICO que o RBAC 45.12-I(a)
exige para operar sob aquele regulamento. A evidência (quem aparece, a
estabilidade entre meses, a correção observada na HELISUL e o alinhamento
total no par 121) aponta para preenchimento incompleto do
`TRANSPREGULAR135` pela fonte — e não para 1.007 infrações. As exceções
verdadeiramente alinhadas (grupo A) coincidem com operadores cujo campo foi
recentemente corrigido.

Recomendação: tratar `transp_reg_135='N'` como "não informado pela fonte"
nas análises, não como "sem inscrição"; revisitar após novos snapshots para
ver se a correção observada na HELISUL se generaliza.

Dicionário de dados oficial da ANAC para `TRANSPREGULAR135`: não localizado
(busca web vazia em 30/09/2026) — lacuna de documentação registrada.

## Reprodução

```bash
cd C:\Users\edils\anac-db
.venv\Scripts\python.exe - <<'PY'
import sqlite3
c = sqlite3.connect("build/anac.db")
print(c.execute("""
  SELECT SUM(p.operacao_135='S' AND p.transp_reg_135='S'),
         SUM(p.operacao_135='S' AND p.transp_reg_135='N')
  FROM participacao p
  WHERE p.papel='OPERADOR' AND p.snapshot_mes='2026-09'""").fetchone())
PY
```
