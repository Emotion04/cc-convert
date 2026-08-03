#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Save Docker images to tar files for offline use

set -e

OUTPUT_DIR="${OUTPUT_DIR:-./docker-images}"
VERSION="${VERSION:-latest}"

echo "Saving Docker images to $OUTPUT_DIR..."
mkdir -p "$OUTPUT_DIR"

# Build images first
echo "Building images..."
docker-compose -f docker-compose.build.yml build

# Save CC Convert image
echo "Saving CC Convert proxy image..."
docker save cc-convert-ccconvert:latest > "$OUTPUT_DIR/ccconvert-$VERSION.tar"

# Save Claude Code image
echo "Saving Claude Code image..."
docker save cc-convert-claude-code:latest > "$OUTPUT_DIR/claude-code-$VERSION.tar"

# Create load script
cat > "$OUTPUT_DIR/load-images.sh" << 'EOF'
#!/bin/bash
# Load Docker images from tar files

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Loading Docker images..."

docker load < "$SCRIPT_DIR/ccconvert-latest.tar"
docker load < "$SCRIPT_DIR/claude-code-latest.tar"

echo "Images loaded successfully!"
echo ""
echo "Tagged as:"
echo "  - cc-convert/cc-convert:latest"
echo "  - cc-convert/claude-code:latest"
EOF

chmod +x "$OUTPUT_DIR/load-images.sh"

echo ""
echo "Images saved to $OUTPUT_DIR:"
ls -lh "$OUTPUT_DIR/"
echo ""
echo "To load images on another machine:"
echo "  cd $OUTPUT_DIR && ./load-images.sh"
