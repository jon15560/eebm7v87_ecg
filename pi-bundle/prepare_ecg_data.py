"""
prepare_ecg_data.py
--------------------
One-time (or occasional) data-prep step for the ECG BLE streamer.

Downloads the MIT-BIH Arrhythmia Database (mitdb) records used by the
MATLAB feature-extraction pipeline (100, 105, 106, 209, 220), resamples
each from its native 360 Hz to 250 Hz, and caches the result to disk as
.npy files so the Raspberry Pi streaming script never has to touch
PhysioNet during a live demo.

Run this once (or whenever you change RECORDS) before running
ecg_ble_peripheral.py.

Usage:
    python3 prepare_ecg_data.py
"""

import os
import numpy as np
import wfdb
from scipy.signal import resample_poly

# Same records used in run_project.m -> keeps this pipeline consistent
# with the already-validated feature/classifier work.
RECORDS = ['100', '105', '106', '209', '220']

PHYSIONET_DB = 'mitdb'          # MIT-BIH Arrhythmia Database
NATIVE_FS = 360                 # Hz, native sampling rate of mitdb
TARGET_FS = 250                 # Hz, required by the assignment
CACHE_DIR = 'ecg_cache'

# resample_poly needs an integer up/down ratio: 250/360 = 25/36
UP, DOWN = 25, 36


def download_and_resample(record_name: str) -> np.ndarray:
    """Download one record from PhysioNet and resample lead 0 to 250 Hz."""
    print(f'  downloading {record_name} from {PHYSIONET_DB} ...')
    record = wfdb.rdrecord(record_name, pn_dir=PHYSIONET_DB)

    # p_signal shape: (n_samples, n_leads). Use the first lead (typically
    # MLII) -- this matches what most MIT-BIH beat-classification work,
    # including read_mitbih.m, is built on.
    signal_360 = record.p_signal[:, 0].astype(np.float64)

    signal_250 = resample_poly(signal_360, UP, DOWN)

    print(f'    {len(signal_360)} samples @ {NATIVE_FS} Hz '
          f'-> {len(signal_250)} samples @ {TARGET_FS} Hz')
    return signal_250.astype(np.float32)


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)

    print(f'Preparing {len(RECORDS)} records from {PHYSIONET_DB} '
          f'at {TARGET_FS} Hz\n')

    manifest = []
    for rec in RECORDS:
        out_path = os.path.join(CACHE_DIR, f'{rec}_250hz.npy')
        if os.path.exists(out_path):
            print(f'  {rec}: cache already exists, skipping download')
        else:
            sig = download_and_resample(rec)
            np.save(out_path, sig)
            print(f'    wrote {out_path}')
        manifest.append(f'{rec}_250hz.npy')

    with open(os.path.join(CACHE_DIR, 'manifest.txt'), 'w') as f:
        f.write('\n'.join(manifest) + '\n')

    print(f'\nDone. Cached records are in ./{CACHE_DIR}/')
    print('Run ecg_ble_peripheral.py next.')


if __name__ == '__main__':
    main()
