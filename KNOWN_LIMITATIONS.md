# Limitações conhecidas

- Hooks Codex e Claude foram validados por payloads simulados e confronto com documentação oficial; não houve execução dentro de hosts Codex/Claude reais nesta máquina. Os resultados não são apresentados como prova live-host.
- A validação dinâmica principal ocorreu em Windows 11, Python 3.14 e Git 2.55. Comandos POSIX/Linux e o uso de `python3` em project scope foram inspecionados estaticamente, não executados em Linux/macOS.
- Junctions e hard links foram testados no Windows. Symlinks dependentes de privilégio foram simulados quando o ambiente não permitiu criá-los; o comportamento POSIX de symlink não foi executado nativamente.
- Interrupções foram injetadas como `KeyboardInterrupt` em processo, não geradas por sinal real do terminal ou encerramento abrupto do sistema operacional. Queda de energia entre operações não pode ser provada pelo harness.
- O scanner Markdown é deliberadamente determinístico e não pretende implementar toda a gramática CommonMark; cobre os casos reproduzidos da matriz (inline, imagem, referência, parênteses balanceados e exclusão de código).
- Métricas de custo usam bytes UTF-8 e palavras separadas por espaço. Tokens e custo financeiro variam conforme modelo, tokenizer, contexto do host e conteúdo do repositório.
- O toolkit evita sobrescrever árvores estrangeiras/modificadas. Nesses casos, atualização ou remoção requer intervenção manual consciente em vez de merge automático.
