"""Raspberry Pi 5 + Hailo-10H + PiSugar Whisplay HAT integration package.

Provides hardware discovery and control for the Brahma AI Lite embedded build:
  - pi.platform: detect whether we're on a Pi
  - pi.whisplay_audio: WM8960 I2S mic/speaker via sounddevice
  - pi.whisplay_display: SPI TFT status display via luma.lcd
  - pi.hailo_engine: Hailo-10H NPU inference backend
  - pi.llm_adapter: Hailo NPU -> _llm.py interface adapter
"""
