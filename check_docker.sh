#!/bin/bash
echo "=== Docker diagnosis ==="
echo "Socket exists: $(ls -la /Users/miserver/.colima/default/docker.sock 2>&1)"
echo ""
echo "--- docker (no flags) ---"
docker ps 2>&1 | head -3
echo "exit: $?"
echo ""
echo "--- docker -H (quoted) ---"
docker -H "unix:///Users/miserver/.colima/default/docker.sock" ps 2>&1 | head -3
echo "exit: $?"
echo ""
echo "--- docker context ls ---"
docker context ls 2>&1
echo ""
echo "--- colima status ---"
colima status 2>&1
echo ""
echo "--- DOCKER_HOST env ---"
echo "DOCKER_HOST=$DOCKER_HOST"
