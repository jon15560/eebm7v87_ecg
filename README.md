**EEBM 7v87** Special Topics in Telemedicine is an Electrical Engineering graduate class at the University of Texas at Dallas taught by Professor Lakshman Tamil. This repository contains the code, report and installation steps for the project. 

Arya Hussein and Jonathan Wong Summer 2026

The capstone project requirements are as follows:
1) Create a server that downloads ECG data from the Physionet MIT-BIH database: https://physionet.org/content/mitdb/1.0.0/. The server can be a desktop or a Raspberry Pi. 
2) Resample the ECG data at 250Hz. The original ECG data from the database is sampled at 360Hz.
3) Transmit the resampled ECG data via Bluetooth.
4) Develop a mobile app that reads the ECG data via Bluetooth and displays the heartbeat
5) Create another server that the mobile app communicates with via Wifi or 3G/4G. This server can be a desktop or cloud server.
6) Use the server to classify the heartbeat as normal or arrhythmia. Pass the classification back to the mobile phone and display the classification onto the data.
7) Create a seamless telemedicine system from start to finish. 

This project demonstrates the telemedicine system using:
- Raspberry Pi 4B running Debian OS 12 Bookworm as the Bluetooth server
- Android mobile app that reads ECG data via Bluetooth and classifies ECG data via Wifi
- Windows 11 Desktop as the Wifi classification server