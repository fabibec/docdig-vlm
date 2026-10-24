#!/bin/bash

mkdir -p models

# Download Q8_0 LLM (812 MB) + BF16/F16 Vision Tower (205 MB) + official prompt
uvx --from huggingface-hub hf download MDfox/OvisOCR2-GGUF-SOTA-20260823 \
  OvisOCR2-Q8_0.gguf \
  mmproj-OvisOCR2-BF16.gguf \
  prompt.txt \
  --local-dir ./models

