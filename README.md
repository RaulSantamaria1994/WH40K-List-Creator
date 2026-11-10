# WH40K List Creator

Skill de Claude para crear **listas de ejército de Warhammer 40,000 (11ª edición)** legales y
optimizadas según lo que necesita el jugador: facción, puntos, miniaturas que tiene, estilo de
juego y rival. Usa los datos de la comunidad [BSData/wh40k-11e](https://github.com/BSData/wh40k-11e)
(puntos, datasheets, armas, destacamentos y mejoras), cálculos de daño esperado (mathhammer) y un
optimizador. Devuelve las listas **solo en pantalla**.

> Proyecto no oficial. Warhammer 40,000 es una marca de Games Workshop. Los datos son de BSData,
> mantenidos por la comunidad.

## Qué hace

Pídele a Claude algo como:

- «Hazme una lista de Necrones a 2000 puntos para torneo, equilibrada»
- «Tengo 1 Captain, 10 Intercessors, 5 Terminators y un Repulsor: la mejor lista de 1000 pts, juego agresivo»
- «Lista de Tiránidos a 2000 para ganar a mi colega que juega Imperial Knights, me gustan los monstruos»
- «¿Es legal esta lista? ¿Cuántos puntos suma?»

Y la skill:

1. Pregunta solo lo que falte (la facción es lo único imprescindible; el resto tiene valores por defecto que se indican en la respuesta).
2. Descarga los datos actuales de BSData.
3. Elige destacamentos dentro del presupuesto de Detachment Points.
4. Mide cada unidad: daño contra 5 objetivos de referencia, aguante, OC y valor por cada 100 puntos.
5. Busca combinaciones con un optimizador que respeta tu colección, los límites de copias y tu estilo.
6. Revisa sinergias (líderes, mejoras, reglas del destacamento, transportes, roles).
7. Valida la lista: puntos, DP, etiquetas únicas, límites de copias, mejoras, personajes Leader/Support, equipo de pago
   y cuántos modelos pueden llevar cada arma (p. ej. 1 Chainfist por cada 5 Terminators).
8. Muestra **una sola lista** en pantalla, con explicación por roles y cómo jugarla.

### Ejemplo de salida

Cada respuesta incluye **una sola lista** en el formato de abajo, una explicación por roles con
cifras y cómo jugarla. No propone alternativas ni genera Excel ni otros archivos.

```
Necrons - Strike Force (1995/2000 pts)
Faction: Xenos - Necrons
Battle size: Strike Force | Detachments (3/3 DP): Awakened Dynasty [3]
Force Disposition: Take and Hold | Enhancements: 4/4

OTHER UNITS
  Lychguard x10 (160 pts)
    + Leader: Overlord (110 pts) — Veil of Darkness — Warlord
  ...
TOTAL: 1995 / 2000 pts  —  data: BSData wh40k-11e 8e52807 2026-11-09
✓ Legal by the checks in this script (points, DP, unique tags, unit limits, enhancements, attachments)
```

## Instalación

### En Claude Code (este repositorio)

La skill está en `.claude/skills/wh40k-list-builder/`, así que Claude Code la detecta sola al
trabajar dentro de este repositorio. Clona el repo y abre Claude Code en la carpeta:

```bash
git clone https://github.com/RaulSantamaria1994/WH40K-List-Creator.git
```

```bash
cd WH40K-List-Creator && claude
```

### En tu perfil de Claude (claude.ai / Claude Code en cualquier carpeta)

Empaqueta la skill y súbela, o copia la carpeta a tus skills de usuario:

```bash
cp -r .claude/skills/wh40k-list-builder ~/.claude/skills/
```

### Requisitos

- Python 3.9+
- `git` (recomendado) o acceso HTTPS a `github.com` para descargar los datos la primera vez.
  Los datos se guardan en `~/.cache/wh40k-11e` (configurable con `WH40K_DATA_DIR`) y se refrescan cada 12 h.

## Uso directo de los scripts

Todos los scripts están en `.claude/skills/wh40k-list-builder/scripts/` y se pueden usar sin
Claude. Los nombres de facción se buscan de forma flexible (`necrons`, `blood angels`, `guard`,
`tau`, `"Space Marines (Ultramarines)"`…).

| Script | Para qué |
|---|---|
| `bsdata.py` | Descarga y compila BSData; lista facciones, unidades, datasheets, destacamentos y límites |
| `metrics.py` | Mathhammer por unidad, y comparación de listas completas (`--compare`) |
| `optimize.py` | Busca combinaciones de unidades bajo un presupuesto de puntos |
| `check_list.py` | Calcula puntos, valida e imprime una lista |
| `library.py` | Guarda tus colecciones y las listas validadas en `~/.wh40k` (fuera del repo) |

En Windows el comando suele ser `python` en lugar de `python3`.

```bash
cd .claude/skills/wh40k-list-builder
```

```bash
python3 scripts/bsdata.py units necrons
```

```bash
python3 scripts/bsdata.py detachments necrons
```

```bash
python3 scripts/bsdata.py unit necrons "Overlord,Lychguard"
```

```bash
python3 scripts/metrics.py necrons --per100 --top 20
```

```bash
python3 scripts/optimize.py necrons --points 2000 --must "Overlord,Lychguard:10" --weights "tank=1.5,durability=1"
```

```bash
python3 scripts/check_list.py mi_lista.json
```

```bash
python3 scripts/library.py collection-set tyranids "Termagants=30,Hive Guard=3,Tyrannofex=1"
python3 scripts/optimize.py tyranids --points 1000 --owned saved
python3 scripts/library.py save-list mi_lista.json
python3 scripts/library.py lists --faction tyranids
python3 scripts/library.py show "nombre de la lista"
```

La biblioteca vive en `~/.wh40k` (o en `$WH40K_HOME`), no dentro del repositorio, para que
tus listas y tu colección no se suban a GitHub. Claude guarda ahí cada lista que valida y
la colección que le das, y las reutiliza cuando le pides cambiar "la lista que me diste".

### Formato de lista (JSON)

```json
{
  "title": "Principal - Awakened Dynasty",
  "faction": "necrons",
  "battle_size": "Strike Force",
  "detachments": ["Awakened Dynasty"],
  "force_disposition": "Take and Hold",
  "units": [
    {"name": "Overlord", "enhancement": "Veil of Darkness", "attached_to": "Lychguard", "warlord": true, "wargear": "Voidscythe"},
    {"name": "Lychguard", "models": 10},
    {"name": "Necron Warriors", "models": 20}
  ]
}
```

- `attached_to`: la `label` (o el nombre) de la unidad guardaespaldas.
- `wargear`: texto libre; las opciones con coste en los datos (p. ej. `Rupture cannon` +20) se cobran si aparecen aquí.
  Pon la cantidad delante (`"4x Khornate eviscerator, 2x Plasma pistol"`): se comprueba contra los
  límites del datasheet para ese tamaño de unidad.
- `composition` (opcional): modelos por tipo, `{"Berzerker w/ eviscerator and bolt pistol": 4}`.
- `loadout` (opcional, para métricas): `"3xGauss destructor,3xClose combat weapon"`; también se valida.

## Reglas de 11ª edición que aplica

| Tamaño | Puntos | Detachment Points | Mejoras | Copias por unidad |
|---|---|---|---|---|
| Incursion | 1000 | 2 | 2 | 2 (4 Battleline) |
| Strike Force | 2000 | 3 | 4 | 3 (6 Battleline) |
| Onslaught | 3000 | 4 | 4 | 3 (no confirmado) |

Además: recargo de puntos por copias repetidas de un datasheet, etiquetas únicas de destacamento,
Force Disposition, un Leader y un Support por unidad (Support siempre adjunto), mejoras tipo
Upgrade en unidades no personaje, Epic Heroes únicos y sin mejoras. Detalle en
[`references/army-rules-11e.md`](.claude/skills/wh40k-list-builder/references/army-rules-11e.md).

## Limitaciones

- **Las estratagemas no están en BSData**; si la skill las menciona, lo advierte como conocimiento general.
- El texto de las **Force Dispositions** tampoco está en los datos.
- Las métricas son **medias para comparar unidades**: suponen media distancia. Las reglas de
  destacamento, auras y líderes (Lethal Hits, +1 a impactar, repeticiones, +OC, FNP…) solo
  cuentan si se pasan con `--mods` (p. ej. `--mods "lethal@vehicle,monster"` o
  `--mods "MONSTER: oc+2"`); lo demás Claude lo añade al razonar y lo dice.
- Los modelos con muchas armas (tanques, monstruos) se infravaloran salvo que se indique su `loadout`.
- Los límites de wargear salen de las restricciones de BSData: si una depende de otras elecciones
  (otra arma, el destacamento) se avisa en vez de dar error, y unas pocas unidades cuya estructura
  en los datos no encaja con su tamaño no se comprueban (también se avisa).
- El límite de copias en Onslaught no está confirmado públicamente (se asume 3).
- Errores de los propios datos de BSData pasan a la skill (p. ej. el destacamento Deathwatch
  Support lista mejoras de otros capítulos); los scripts avisan de lo que detectan.

## Estructura

```
.claude/skills/wh40k-list-builder/
├── SKILL.md                      # flujo de trabajo que sigue Claude
├── scripts/                      # bsdata, metrics, optimize, check_list, library
├── references/
│   ├── army-rules-11e.md         # reglas de construcción de ejército
│   ├── list-strategy.md          # roles, enfrentamientos, cómo leer las métricas (opcional)
│   └── mods.md                   # sintaxis de --mods para modelar reglas de destacamento
└── evals/                        # casos de prueba (formato claude plugin eval)
```

## Pruebas

`evals/` contiene 3 casos (Necrones 2000 competitivo, Ultramarines 1000 con colección propia y
estilo agresivo, Tiránidos contra Imperial Knights) con sus criterios de evaluación. En la última
iteración la skill superó todos los criterios (100 %, frente al 76 % sin la skill), con un ~30 %
menos de tiempo y ~20 % menos de tokens. Para ejecutarlos con Claude Code (ejecuciones reales de
pago):

```bash
cd .claude/skills/wh40k-list-builder && claude plugin eval . --allow-tools Write,Edit,Bash
```
