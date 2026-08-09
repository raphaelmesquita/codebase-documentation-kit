# Changelog — Codebase Documentation Kit V2 Tested

Data da release: 2026-08-09

## Segurança de migração e rollback

- Migração conhecida V1 agora remove apenas linhas canônicas, preserva bytes/CRLF não relacionados e exige revisão semântica para referências ou layouts ambíguos.
- Migração já V2 tornou-se no-op idempotente, sem criação repetida de backups.
- Escritas de migração e rollback são atômicas, rejeitam escapes, symlinks/reparse points e hard links perigosos.
- Falhas de escrita, validação e interrupções (`KeyboardInterrupt`/`SystemExit`) acionam compensação antes de retornar ou propagar a interrupção.
- Rollback detecta edições posteriores, deleções e conflitos; uma recuperação automática incompleta registra um baseline residual para permitir retry seguro sem autorizar sobrescrita de edições subsequentes.
- Falha dupla durante rollback preserva a exceção de interrupção, anexa o diagnóstico da compensação e mantém o backup reutilizável.

## Detecção de impacto e validação

- Estado Git não disponível é explicitamente indeterminado, nunca tratado como “sem impacto”.
- Snapshot agora compara status, hash da worktree e identidade dos objetos no índice Git, cobrindo mudanças staged com status `MM` inalterado.
- `dist/`, `build/`, minificados e artefatos equivalentes são classificados como gerados antes das regras genéricas de código.
- Scanner Markdown cobre links inline, imagens e referências com parênteses balanceados, ignorando código.
- `.docsctl.json` malformado produz diagnóstico estruturado sem traceback.
- Dívida de validação é contabilizada como multiconjunto; uma nova ocorrência idêntica é detectada.
- Snapshot ausente ou corrompido resulta em continuação explícita e não em rebaseline silencioso.
- Stop hook reutiliza o resultado de validação quando não há impacto, reduzindo trabalho determinístico duplicado.

## Instalação e integrações

- Instalador passou a usar manifests de ownership e hashes de árvore; árvores estrangeiras ou modificadas não são substituídas nem removidas.
- Instalação multi-alvo é transacional e restaura o estado anterior em falhas de destino, configuração ou interrupção.
- Preflight cobre todos os destinos antes da primeira mutação e bloqueia junction/reparse escape em project scope.
- Ownership dos hooks é estreito e independente do caminho do interpretador Python.
- Instalar/remover Codex e Claude separadamente preserva runtime compartilhado e configurações não pertencentes ao toolkit.
- Required paths que viram diretórios ou ficam ilegíveis geram feedback de hook válido e determinístico.

## Instruções e testes

- O architect deriva o escopo real de provedores; Claude-only não habilita Codex implicitamente.
- Adicionados 32 testes de regressão ao baseline de 14 testes, totalizando 46.
- O pacote foi validado novamente a partir de uma extração limpa do próprio ZIP final.
