# Installation & Environment Setup Guide

This guide details the complete installation procedure for the **Honeywell Residential Cable Ring Optimization Engine**, covering cross-platform prerequisites, virtual environment configuration, dependency installation, verification checks, and platform-specific tips.

---

## 1. System Requirements

| Specification | Minimum | Recommended |
| :--- | :--- | :--- |
| **Operating System** | Windows 10/11 (64-bit), Ubuntu 20.04+, macOS 12+ | Windows 11 (64-bit) or Ubuntu 22.04 LTS |
| **Python** | Python 3.10.x | Python 3.10, 3.11, or 3.12 |
| **CPU** | 4 physical cores | 8+ physical cores (Intel 13th/14th Gen or AMD Ryzen 7000+) |
| **RAM** | 8 GB | 16 GB - 32 GB (recommended for large 17,000 plan batch audits) |
| **Disk Space** | 2 GB for engine & tests | 10 GB+ if retaining full 17k batch run results and pickle artifacts |
| **GPU** | Not required (CPU vectorized) | NVIDIA GPU (CUDA 11.8+ / 12.x) for optional PPO research checkpoints |

---

## 2. Quickstart Installation

### Step 1: Clone the Repository

```bash
git clone https://github.com/KPB040107/honeywell-cable-ring-optimization.git
cd honeywell-cable-ring-optimization
```

### Step 2: Create and Activate a Virtual Environment

Isolate dependencies using Python's standard `venv` module:

#### On Windows (PowerShell):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

> **Note for Windows PowerShell**: If script execution is restricted, enable it for the current session:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
> ```

#### On Windows (Command Prompt):
```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

#### On Linux or macOS (Bash / Zsh):
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Step 3: Upgrade Package Managers

Ensure `pip`, `setuptools`, and `wheel` are up to date:

```bash
python -m pip install --upgrade pip setuptools wheel
```

### Step 4: Install Dependencies & Editable Package

Install the repository requirements and register the `resplan_router` package in editable mode (`-e .`):

```bash
pip install -r requirements.txt
pip install -e .
```

---

## 3. Dependency Overview

The system depends on battle-tested numerical, geometric, optimization, and visualization packages:

| Dependency | Purpose |
| :--- | :--- |
| `shapely >= 2.0.0` | High-performance 2D planar computational geometry (GEOS C-API bindings) |
| `numpy >= 1.24.0` | Vectorized distance matrices, grid coordinates, and tensor indexing |
| `scipy >= 1.10.0` | Spatial algorithms, KD-trees, and Wilcoxon statistical significance testing |
| `networkx >= 3.0` | Dual-graph planar topologies, minimum spanning trees, and cycle discovery |
| `ortools >= 9.8` | CP-SAT combinatorial constraint programming and MILP partitioning solvers |
| `matplotlib >= 3.7.0` | Architectural schematics, overlay rendering, and benchmark scorecards |
| `pydantic >= 2.0.0` | Strict data validation and schema serialization contracts |
| `fastapi` / `uvicorn` | Lightweight HTTP server for the interactive Co-Design Studio web HUD |
| `pytest >= 7.4.0` | Unit, integration, negative regression, and property test execution |

---

## 4. Verifying the Installation

### Verification 1: Unit Test Suite Execution

Run the fast unit test suite to verify all core algorithms, geometry primitives, and contracts:

```bash
pytest tests/unit/ -q
```

Expected output:
```text
301 passed in 4.50s
```

### Verification 2: Run Single-Plan Quickstart

Execute the self-contained optimization example on the sample floor plan:

```bash
python examples/quickstart_optimize.py
```

Expected output confirms 100% hard rule compliance:
```text
======================================================================
HONEYWELL CABLE RING ROUTER - SINGLE PLAN OPTIMIZATION
======================================================================
Plan ID:                sample_residential_floor_plan
Validity:               PASSED (100% compliant)
Hubs Placed (H):        2
Total Cable Length (L): 42.48 m
90° Bends (B):          18
Crossings (X):          1
Overlap (O):            0.00 m
Official Cost J:        56.48
======================================================================
```

### Verification 3: CLI Sanity Check

Invoke the CLI directly:

```bash
python -m resplan_router.cli --input examples/sample_floor_plan.json
```

---

## 5. Platform-Specific Setup Notes

### Windows 10 & 11

1. **UTF-8 Console Encoding:**
   To properly display unicode characters (such as `ΔA_gap`, `m²`, `90°`) in Windows Command Prompt or PowerShell, configure your console code page:
   ```cmd
   chcp 65001
   ```
   The engine automatically attempts to set stream reconfigurations, but setting code page 65001 prevents console font distortions.

2. **Long Path Enablement:**
   Deeply nested benchmark directories benefit from Windows Long Path support:
   ```powershell
   New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
     -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
   ```

3. **C++ Build Tools (Optional):**
   Pre-compiled wheels for `shapely` and `scipy` are available on PyPI for Windows x86_64. If building from source, ensure the Microsoft C++ Build Tools (Visual Studio 2022 Build Tools) are installed.

### Linux (Ubuntu / Debian / RHEL)

1. **GEOS & System Libraries:**
   While `shapely >= 2.0` distributes binary wheels with bundled GEOS libraries, system GEOS development packages can be installed if building from source:
   ```bash
   sudo apt-get update
   sudo apt-get install -y libgeos-dev build-essential python3-dev
   ```

2. **Headless Visualization:**
   When running batch benchmarks on a remote headless server without an X11 display, ensure Matplotlib runs in headless backend mode:
   ```bash
   export MPLBACKEND=Agg
   ```

### macOS (Apple Silicon M1/M2/M3 & Intel)

1. **Homebrew Setup:**
   Install Python via Homebrew if needed:
   ```bash
   brew update
   brew install python@3.11 geos
   ```

2. **Architecture Consistency:**
   Ensure your Python virtual environment matches your native terminal architecture (`arm64` for Apple Silicon).

---

## 6. Docker Container Deployment

For containerized cloud workflows, batch evaluation, or automated CI/CD pipelines, use the standard Python 3.11 slim image:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgeos-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .
RUN pip install --no-cache-dir -e .

# Expose interactive studio port
EXPOSE 8000

# Default entrypoint
CMD ["python", "-m", "resplan_router.cli", "--serve", "--host", "0.0.0.0", "--port", "8000"]
```

Build and run:
```bash
docker build -t honeywell-cable-router .
docker run -p 8000:8000 honeywell-cable-router
```
