# WH40K List Creator

Skill de agente que crea listas legales de Warhammer 40,000 (11ª edición). Para pedir una lista,
usa la skill `wh40k-list-builder` y sigue su `SKILL.md`.

## Dónde está la skill

- **Fuente canónica: `.agents/skills/wh40k-list-builder/`**. La leen Cursor, Gemini CLI y
  Codex / ChatGPT desktop.
- `.claude/skills/wh40k-list-builder/` es una **copia generada** para Claude Code. No la edites.

Tras cualquier cambio en la skill:

```bash
python tools/sync_skill.py
```

`python tools/sync_skill.py --check` falla si la copia está desactualizada.

## Reglas para editar

- Los scripts usan solo la biblioteca estándar de Python 3.9+.
- Los comandos de `SKILL.md` van en una sola línea (sin `\`), para que funcionen en PowerShell.
- No inventes puntos, reglas ni estadísticas: salen de BSData (ver `references/`).
- La biblioteca del usuario vive en `~/.wh40k`, nunca dentro del repositorio.
