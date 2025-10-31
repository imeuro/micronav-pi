#!/usr/bin/env python3
"""
Script per pulire un file JSON mantenendo solo i nodi specificati.
Utilizzo: python3 clean_nodes.py <file_input> [<file_output>]
Se file_output non è specificato, il file viene salvato con suffisso "_cleaned.json".
"""

import json
import sys
import os


# Nodi da mantenere (whitelist)
NODES_TO_KEEP = [
    'id',
    'landkreis',
    'ort',
    'strasse',
    'vmax',
    'art',
    'land',
    'status',
    'lat',
    'lng',
    'type'
]


def clean_nodes(obj, is_top_level=False):
    """Mantiene solo i nodi specificati nella whitelist e rimuove tutti gli altri"""
    if isinstance(obj, dict):
        # Se è il livello top-level e ha la chiave "result", mantieni "result" e processa il contenuto
        if is_top_level and 'result' in obj:
            return {'result': clean_nodes(obj['result'])}
        # Altrimenti, mantiene solo le chiavi presenti nella whitelist e processa ricorsivamente
        new_obj = {k: clean_nodes(v) for k, v in obj.items() if k in NODES_TO_KEEP}
        return new_obj
    elif isinstance(obj, list):
        # Itera sugli elementi della lista
        return [clean_nodes(item) for item in obj]
    else:
        # Per valori primitivi, ritorna invariato
        return obj


def main():
    if len(sys.argv) < 2:
        print("Utilizzo: python3 clean_nodes.py <file_input> [<file_output>]")
        print("Se file_output non è specificato, il file viene salvato con suffisso '_cleaned.json'.")
        sys.exit(1)
    
    input_file = sys.argv[1]
    
    if not os.path.exists(input_file):
        print(f"Errore: Il file {input_file} non esiste.")
        sys.exit(1)
    
    # Genera automaticamente il nome del file di output con suffisso "_cleaned.json"
    if len(sys.argv) > 2:
        output_file = sys.argv[2]
    else:
        # Estrae il nome del file senza estensione e aggiunge "_cleaned.json"
        base_name = os.path.splitext(input_file)[0]
        output_file = f"{base_name}_cleaned.json"
    
    print(f"Lettura del file {input_file}...")
    
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        print(f"Mantenendo solo i nodi: {', '.join(NODES_TO_KEEP)}...")
        data_cleaned = clean_nodes(data, is_top_level=True)
        
        print(f"Salvataggio del file pulito in {output_file}...")
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data_cleaned, f, ensure_ascii=False, separators=(',', ':'))
        
        print("Operazione completata!")
        
    except json.JSONDecodeError as e:
        print(f"Errore nel parsing del JSON: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Errore: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

