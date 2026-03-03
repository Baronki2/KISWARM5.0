#!/bin/bash
#
# KISWARM v5.1 — Model Training Script
# Train all Ollama models for KISWARM integration
#
# Usage: ./train_all_models.sh [options]
#
# Options:
#   --all           Train all models
#   --role ROLE     Train specific role (orchestrator, security, solarchase)
#   --dry-run       Show commands without executing
#   --verify-only   Only run verification tests
#
# Author: Baron Marco Paolo Ialongo
# Version: 5.1.0

set -e

# Configuration
KISWARM_HOME="${KISWARM_HOME:-$HOME}"
TRAINING_DIR="$(dirname "$0")/.."
PROMPT_DIR="$TRAINING_DIR/prompts"
DATA_DIR="$TRAINING_DIR/data"
MODEL_DIR="$TRAINING_DIR/modelfiles"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Banner
echo -e "${BLUE}"
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║         KISWARM v5.1 — Model Training System                   ║"
echo "║              Planetary Machine Integration                     ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check dependencies
check_dependencies() {
    echo -e "${YELLOW}Checking dependencies...${NC}"
    
    # Check Ollama
    if ! command -v ollama &> /dev/null; then
        echo -e "${RED}ERROR: Ollama not found. Please install from https://ollama.ai${NC}"
        exit 1
    fi
    echo -e "  ${GREEN}✓${NC} Ollama installed"
    
    # Check for training data
    if [ ! -f "$DATA_DIR/kiswarm_training_qwen.jsonl" ]; then
        echo -e "${RED}ERROR: Training data not found at $DATA_DIR/kiswarm_training_qwen.jsonl${NC}"
        exit 1
    fi
    echo -e "  ${GREEN}✓${NC} Training data found"
    
    # Check for modelfiles
    if [ ! -d "$MODEL_DIR" ]; then
        echo -e "${RED}ERROR: Modelfiles directory not found${NC}"
        exit 1
    fi
    echo -e "  ${GREEN}✓${NC} Modelfiles directory found"
}

# List available models
list_models() {
    echo -e "\n${BLUE}Available base models for training:${NC}"
    echo "  Large (14B+):"
    echo "    - qwen2.5:14b (Master Orchestrator)"
    echo "    - qwen2.5:32b (Swarm Commander)"
    echo ""
    echo "  Medium (7B-13B):"
    echo "    - qwen2.5:7b (Security Specialist)"
    echo "    - qwen2.5:7b-instruct (Industrial Specialist)"
    echo "    - qwen2.5-coder:7b (Tool Forge Engineer)"
    echo ""
    echo "  Small (<7B):"
    echo "    - qwen2.5:3b (Solar Chase Monitor)"
    echo "    - qwen2.5:1.5b (Quick Response Node)"
    echo ""
    echo -e "${YELLOW}Run 'ollama list' to see installed models${NC}"
}

# Train orchestrator models
train_orchestrator() {
    echo -e "\n${BLUE}═══ Training Orchestrator Models ═══${NC}"
    
    # Master Orchestrator (qwen2.5:14b)
    echo -e "${YELLOW}Creating kiswarm-orchestrator from qwen2.5:14b...${NC}"
    if [ "$DRY_RUN" = true ]; then
        echo "  [DRY RUN] ollama create kiswarm-orchestrator -f $MODEL_DIR/Modelfile.orchestrator"
    else
        ollama create kiswarm-orchestrator -f "$MODEL_DIR/Modelfile.orchestrator"
        echo -e "  ${GREEN}✓${NC} Created kiswarm-orchestrator"
    fi
    
    # Swarm Commander (qwen2.5:32b) - if available
    if ollama list | grep -q "qwen2.5:32b"; then
        echo -e "${YELLOW}Creating kiswarm-commander from qwen2.5:32b...${NC}"
        if [ "$DRY_RUN" = true ]; then
            echo "  [DRY RUN] ollama create kiswarm-commander -f $MODEL_DIR/Modelfile.orchestrator"
        else
            sed 's/FROM qwen2.5:14b/FROM qwen2.5:32b/' "$MODEL_DIR/Modelfile.orchestrator" | \
                ollama create kiswarm-commander -f -
            echo -e "  ${GREEN}✓${NC} Created kiswarm-commander"
        fi
    fi
}

# Train security models
train_security() {
    echo -e "\n${BLUE}═══ Training Security Models ═══${NC}"
    
    echo -e "${YELLOW}Creating kiswarm-security from qwen2.5:7b...${NC}"
    if [ "$DRY_RUN" = true ]; then
        echo "  [DRY RUN] ollama create kiswarm-security -f $MODEL_DIR/Modelfile.security"
    else
        ollama create kiswarm-security -f "$MODEL_DIR/Modelfile.security"
        echo -e "  ${GREEN}✓${NC} Created kiswarm-security"
    fi
}

# Train solar chase models
train_solarchase() {
    echo -e "\n${BLUE}═══ Training Solar Chase Models ═══${NC}"
    
    echo -e "${YELLOW}Creating kiswarm-solarchase from qwen2.5:3b...${NC}"
    if [ "$DRY_RUN" = true ]; then
        echo "  [DRY RUN] ollama create kiswarm-solarchase -f $MODEL_DIR/Modelfire.solarchase"
    else
        ollama create kiswarm-solarchase -f "$MODEL_DIR/Modelfire.solarchase"
        echo -e "  ${GREEN}✓${NC} Created kiswarm-solarchase"
    fi
}

# Verify trained models
verify_models() {
    echo -e "\n${BLUE}═══ Verifying Trained Models ═══${NC}"
    
    verify_model() {
        local model=$1
        local test_query="What is KISWARM?"
        
        echo -e "${YELLOW}Testing $model...${NC}"
        
        if ! ollama list | grep -q "$model"; then
            echo -e "  ${RED}✗${NC} Model not found"
            return 1
        fi
        
        # Run test query
        response=$(ollama run "$model" "$test_query" 2>/dev/null | head -5)
        
        if echo "$response" | grep -qi "swarm\|planetary\|eternal"; then
            echo -e "  ${GREEN}✓${NC} Model responds correctly"
            echo -e "  ${BLUE}Sample:${NC} ${response:0:100}..."
        else
            echo -e "  ${RED}✗${NC} Model may need retraining"
            echo -e "  ${BLUE}Response:${NC} ${response:0:100}..."
        fi
    }
    
    # Test each created model
    for model in kiswarm-orchestrator kiswarm-security kiswarm-solarchase; do
        if ollama list | grep -q "$model"; then
            verify_model "$model"
        fi
    done
}

# Generate training report
generate_report() {
    echo -e "\n${BLUE}═══ Training Report ═══${NC}"
    
    REPORT_FILE="$TRAINING_DIR/training_report_$(date +%Y%m%d_%H%M%S).md"
    
    cat > "$REPORT_FILE" << EOF
# KISWARM v5.1 Model Training Report

**Date:** $(date)
**Training Directory:** $TRAINING_DIR

## Models Created

EOF
    
    ollama list | grep kiswarm >> "$REPORT_FILE" || echo "No KISWARM models found" >> "$REPORT_FILE"
    
    cat >> "$REPORT_FILE" << EOF

## Training Data

- Training file: $DATA_DIR/kiswarm_training_qwen.jsonl
- Prompts: $PROMPT_DIR/

## Next Steps

1. Verify models with: \`./train_all_models.sh --verify-only\`
2. Test integration with KISWARM API
3. Deploy to production nodes

---
*Generated by KISWARM v5.1 Training System*
EOF
    
    echo -e "${GREEN}Report saved to: $REPORT_FILE${NC}"
}

# Show usage
usage() {
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  --all           Train all models"
    echo "  --role ROLE     Train specific role (orchestrator, security, solarchase)"
    echo "  --dry-run       Show commands without executing"
    echo "  --verify-only   Only run verification tests"
    echo "  --list          List available models"
    echo "  --help          Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 --all                    # Train all models"
    echo "  $0 --role orchestrator      # Train only orchestrator"
    echo "  $0 --verify-only            # Verify existing models"
}

# Parse arguments
DRY_RUN=false
VERIFY_ONLY=false
ROLE=""
ALL=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --all)
            ALL=true
            shift
            ;;
        --role)
            ROLE="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --verify-only)
            VERIFY_ONLY=true
            shift
            ;;
        --list)
            list_models
            exit 0
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            usage
            exit 1
            ;;
    esac
done

# Main execution
if [ "$VERIFY_ONLY" = true ]; then
    verify_models
    exit 0
fi

check_dependencies

if [ "$ALL" = true ]; then
    train_orchestrator
    train_security
    train_solarchase
elif [ -n "$ROLE" ]; then
    case $ROLE in
        orchestrator) train_orchestrator ;;
        security) train_security ;;
        solarchase) train_solarchase ;;
        *)
            echo -e "${RED}Unknown role: $ROLE${NC}"
            echo "Valid roles: orchestrator, security, solarchase"
            exit 1
            ;;
    esac
else
    # Default: interactive mode
    echo -e "${YELLOW}No role specified. Training all models...${NC}"
    train_orchestrator
    train_security
    train_solarchase
fi

# Verify and report
verify_models
generate_report

echo -e "\n${GREEN}═══ Training Complete ═══${NC}"
echo -e "Run 'ollama list' to see all trained models"
echo -e "Use 'ollama run kiswarm-orchestrator' to interact with the Master Orchestrator"
