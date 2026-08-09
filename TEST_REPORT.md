# Relatório completo de testes — Codebase Documentation Kit V2 Tested

Data: 2026-08-09

Veredito final: **PASS**

Artefato: `codebase-documentation-kit-v2-tested.zip`

## Resumo executivo

Os dois ZIPs em `inputs/` foram tratados como evidência imutável e seus SHA-256 foram confirmados antes do trabalho:

- V1: `1F94BD00DF292EE3BF5C191D50A972DFD2B968CE53B34F9D9CEB70BD0E456A14`
- V2 candidato: `3C6EC1F906843F387A6AC043306AC066C139F1D2CEF634D9650A028C2B4B5704`

Seis revisores iniciais trabalharam sobre cópias isoladas. A suíte original passou 14/14, mas os testes adversariais encontraram falhas reais de migração/rollback, ownership e atomicidade do instalador, detecção de impacto, validação e escopo de provedor. As correções foram integradas com regressões; rodadas adversariais sequenciais adicionais encontraram e fecharam bordas de links/paths, falha transacional, retry e interrupções.

Resultado final na árvore canônica: **46/46 testes passaram**. O próprio ZIP final foi então extraído em um diretório novo e a mesma suíte passou **46/46** em 20,752 s. Uma fixture focada de migração+rollback passou separadamente. Dry-runs user/project criaram **0 arquivos** nos dois alvos.

## Ambiente e fontes oficiais

- Windows 11; Python 3.14; Git 2.55; `core.longpaths=true` durante a suíte.
- OpenAI Skills: estrutura baseada em diretório com `SKILL.md`, nome e descrição confrontada com [Build skills](https://learn.chatgpt.com/docs/build-skills) (acesso em 2026-08-09).
- OpenAI Hooks: locais/configuração, eventos SessionStart/Stop, silêncio em sucesso e resposta de bloqueio confrontados com [Hooks](https://learn.chatgpt.com/docs/hooks) (acesso em 2026-08-09).
- Claude Code Skills/commands: layout e descoberta confrontados com [Slash commands / skills](https://code.claude.com/docs/en/slash-commands) (acesso em 2026-08-09).
- Claude Code Hooks: matchers, Stop e `hookSpecificOutput.additionalContext` confrontados com [Hooks reference](https://code.claude.com/docs/en/hooks) (acesso em 2026-08-09).

Todos os testes de hook foram **simulações de payload**, não execuções em host real.

## Matriz mínima

| ID | Resultado | Evidência principal |
|---|---|---|
| M01 | PASS | V1 conhecido planejado/migrado; integração Codex+Claude |
| M02 | PASS | regras customizadas e bytes/CRLF preservados; referência ambígua não apagada |
| M03 | PASS | migração Claude-only |
| M04 | PASS | custom `CLAUDE.md` exige revisão semântica |
| M05 | PASS | procedure docs com possível fato impedem deleção automática |
| M06 | PASS | segunda migração retorna already-v2 sem novo backup |
| M07 | PASS | restauração exata, remoção de criados e conflitos pós-migração |
| M08 | PASS | falhas, compensação, retry e `KeyboardInterrupt` transacionais |
| I01 | PASS | source modificado sinaliza revisão |
| I02 | PASS | test-only não sinaliza manutenção sem impacto |
| I03 | PASS | dist/build/minificados tratados como gerados |
| I04 | PASS | dirty worktree e mudança apenas no índice são detectadas |
| I05 | PASS | dirty file intocado não é atribuído à sessão |
| I06 | PASS | source untracked detectado |
| I07 | PASS | deleção tracked detectada |
| I08 | PASS | rename tracked preserva significado |
| I09 | PASS | dívida de link preexistente não vira novo blocker |
| I10 | PASS | novo link quebrado inline/imagem/referência vira blocker |
| I11 | PASS | ausência de Git explicitamente indeterminada |
| I12 | PASS | modelo ausente/malformado é seguro e diagnosticado |
| C01 | PASS (simulado) | SessionStart Codex gera snapshot e payload válido |
| C02 | PASS (simulado) | Stop Codex sem impacto é silencioso/mínimo |
| C03 | PASS (simulado) | impacto semântico gera uma continuação direcionada |
| C04 | PASS (simulado) | regressão determinística nova gera blocker exato |
| C05 | PASS | hooks Codex alheios preservados no ciclo completo |
| A01 | PASS (simulado) | SessionStart Claude válido |
| A02 | PASS (simulado) | Stop Claude sem impacto é silencioso/mínimo |
| A03 | PASS (simulado) | feedback adicional/continuação Claude conforme protocolo |
| A04 | PASS | settings/hooks Claude alheios preservados |
| P01 | PASS | dry-run user both; zero entradas criadas |
| P02 | PASS | dry-run project both; zero entradas criadas |
| P03 | PASS | Codex depois Claude com runtime compartilhado |
| P04 | PASS | Claude depois Codex com runtime compartilhado |
| P05 | PASS | uninstall de um alvo preserva o outro |
| P06 | PASS | reinstall/update idempotente e transacional |
| P07 | PASS | caminhos com espaços e troca de interpretador |
| P08 | PASS | ZIP com raiz única, 28 entradas, extração limpa |
| P09 | PASS | 46/46 testes do ZIP extraído |
| S01 | PASS | frontmatter/nome/descrição e launchers válidos |
| S02 | PASS | maintainer 1.794 bytes vs. V1 25.631 bytes |
| S03 | PASS | referência pesada só no fluxo de arquitetura |
| S04 | PASS | root docs não obrigam architect após toda tarefa |
| S05 | PASS (simulado) | hooks de sucesso sem contexto desnecessário |

## Regressões acrescentadas

32 testes foram adicionados ao baseline de 14, cobrindo: preservação byte-a-byte; layouts ambíguos; no-op V2; rollback conflict-aware; path traversal; symlink/reparse/hardlink; restauração após falhas de múltiplas etapas; retry após recuperação falha; interrupções e dupla falha; índice Git staged; classificação generated; parser Markdown; modelo malformado; dívida duplicada; snapshot ausente/corrompido; required paths inválidos; reutilização da validação; ownership de árvores/hooks; preflight; transação do instalador; junction escape; troca de interpretador.

## Rodadas adversariais e disposição

- Revisão inicial A–F: REQUEST_CHANGES; todos os achados reproduzíveis foram triados.
- Rodadas 1–5: corrigiram deleções/conflitos de rollback, dívida duplicada, snapshot ausente, junction/hardlink, CRLF, atomicidade, ownership de interpretador e retry público.
- Rodada 6: encontrou interrupção parcial em migração/rollback/installer e mudança invisível no índice; corrigidos com três regressões.
- Rodada 7: encontrou dupla falha que engolia `KeyboardInterrupt`; corrigida com propagação, nota diagnóstica, baseline residual e regressão.
- Alegação de que Claude `additionalContext` seria inválido foi rejeitada após confronto com a documentação oficial atual, que o aceita como feedback não bloqueante.

## Gate do artefato final

- Tamanho: 58.967 bytes.
- SHA-256: `E2ACDA27C1A0DDBCF40C9A35829081FBFBCA4C73586D850E0746FE5F20CDB6D9`.
- 28 entradas ZIP; 26 arquivos de toolkit.
- Nenhum `__pycache__`, `.pytest_cache`, `.pyc`, `.pyo` ou `.git` no ZIP.
- Nenhum segredo, credencial ou caminho absoluto específico da máquina encontrado na varredura textual.
- Extração nova do ZIP: 46/46 testes PASS.
- Teste focal `test_migration_rollback_restores_and_removes_created_marker`: PASS.
- Dry-run user both: PASS, 0 escritas.
- Dry-run project both: PASS, 0 escritas.

As limitações de ambiente e cobertura estão separadas em `KNOWN_LIMITATIONS.md`.
