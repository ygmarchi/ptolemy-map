# Ptolemy Map Warp (Python)

Progetto base per:

1. partire da punti geografici (`lat`, `lon`)
2. mostrarli su mappa spoglia (contorno terre emerse + punti)
3. applicare una trasformazione continua
4. trascinare con la stessa trasformazione anche il contorno delle terre emerse
5. renderizzare il risultato come animazione GIF
6. esportare anche una versione SVG animata

## Requisiti

- Python 3.11+
- Accesso internet al primo avvio per scaricare il dataset Natural Earth tramite `geodatasets`

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Input punti

I dati sono letti da `data/Coordinate città campione.csv`.

Colonne usate:

- `Città`
- `Latitudini reali`
- `Longitudini reali da Greenwich` (interpretabile come longitudini reali)

Le coordinate nel file sono in formato gradi/minuti/secondi (es. `41°54’`, `5°21’W`) e vengono convertite in gradi decimali.

Di default il mainland americano viene rimosso dalla renderizzazione. Se vuoi visualizzarlo, usa `--show-americas`.
La mappa include layer dedicati per Canarie e Piccole Antille durante la transizione.

## Esecuzione

```bash
python3 run.py
```

Output di default: `output/ptolemy_warp.gif`

Per generare SVG animato:

```bash
python3 run.py --output output/ptolemy_warp.svg
```

Di default la trasformazione e' rallentata di 5x, il frame finale viene mantenuto visibile per un breve tempo e la GIF non e' in loop infinito.
Le label sono ridotte automaticamente per migliorare la leggibilita'.

## Parametri utili

```bash
python3 run.py \
  --points "data/Coordinate città campione.csv" \
  --output output/warp_v2.gif \
  --frames 100 \
  --fps 25 \
  --lon-factor 1.428 \
  --slowdown-factor 5 \
  --end-hold-seconds 1.5 \
  --gif-loop 1 \
  --max-labels 18 \
  --show-americas
```

## Come funziona la trasformazione

- I punti sono trasformati in modo esplicito (target):
  - Regola standard: `lon' = lon * 1.428`, `lat' = lat`.
  - `Isole Canarie` e `Arrecife`: oltre al fattore longitudine, `lat' = lat - 15`.
  - `Thule Orientale (fittizia)`: nessun fattore longitudine, ma `lon' = lon + 48.5`.
- Le terre emerse sono deformate con una Thin Plate Spline globale, calibrata sugli stessi punti di controllo.
- Le Piccole Antille sono aggiunte alla mappa e durante la transizione migrano verso le longitudini delle Canarie.
- Le Canarie iniziano a svanire poco prima della fine e sono completamente trasparenti all'ultimo frame.
- I punti città vengono deformati con lo stesso identico campo elastico delle terre emerse (ancore esatte sui punti di controllo), cosi' restano solidali al terreno sottostante.
- L'animazione interpola da identita (`t=0`) a trasformazione completa (`t=1`).
- La vista viene croppata automaticamente sulla zona coperta dai punti iniziali/finali (dataset + punti aggiunti), con un piccolo margine.
- `--slowdown-factor` rallenta l'evoluzione della trasformazione senza cambiare il modello geometrico.
- `--end-hold-seconds` mantiene fermo il frame finale prima della chiusura dell'animazione (clamp automatico a massimo 15s).
- `--max-labels` limita il numero di etichette visualizzate (`0` = nessuna etichetta).
- Di default il mainland americano e' nascosto; usa `--show-americas` per riattivarlo.

## Struttura

- `run.py`: entrypoint CLI
- `src/ptolemy_map/warp.py`: campo di deformazione e warp geometrie
- `src/ptolemy_map/animate.py`: caricamento dati e rendering GIF
- `data/Coordinate città campione.csv`: dataset sorgente
