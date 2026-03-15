#!/usr/bin/env bash
# Run this from skillforge/ directory:
# bash add_dna_page.sh

set -e
FILE="frontend/index.html"

echo "🧬 Adding Skill DNA page to $FILE..."

# ── 1. Add DNA nav item after Team Hub nav item ───────────────
OLD_NAV='      <div class="nav-section-label" style="margin-top:6px">Team</div>'
NEW_NAV='      <div class="nav-section-label" style="margin-top:6px">Identity</div>
      <div class="nav-item" :class="{active:view==='"'"'dna'"'"'}" @click="go('"'"'dna'"'"')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2C6 2 2 7 2 12s4 10 10 10"/><path d="M12 22c6 0 10-5 10-10S18 2 12 2"/><path d="M2 12h4M18 12h4M12 2v4M12 18v4"/><circle cx="12" cy="12" r="3"/></svg>
        Skill DNA
        <span style="margin-left:auto;font-size:9px;font-weight:700;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;padding:2px 6px;border-radius:8px">NEW</span>
      </div>
      <div class="nav-section-label" style="margin-top:6px">Team</div>'

python3 -c "
import sys
content = open('$FILE').read()
old = '''      <div class=\"nav-section-label\" style=\"margin-top:6px\">Team</div>'''
new = '''      <div class=\"nav-section-label\" style=\"margin-top:6px\">Identity</div>
      <div class=\"nav-item\" :class=\"{active:view===\\'dna\\'}\" @click=\"go(\\'dna\\')\">
        <svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\"><path d=\"M12 2C6 2 2 7 2 12s4 10 10 10\"/><path d=\"M12 22c6 0 10-5 10-10S18 2 12 2\"/><path d=\"M2 12h4M18 12h4M12 2v4M12 18v4\"/><circle cx=\"12\" cy=\"12\" r=\"3\"/></svg>
        Skill DNA
        <span style=\"margin-left:auto;font-size:9px;font-weight:700;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;padding:2px 6px;border-radius:8px\">NEW</span>
      </div>
      <div class=\"nav-section-label\" style=\"margin-top:6px\">Team</div>'''
if old in content:
    content = content.replace(old, new, 1)
    open('$FILE', 'w').write(content)
    print('✅ Nav item added')
else:
    print('❌ Nav anchor not found')
    sys.exit(1)
"

# ── 2. Add DNA view HTML before </main> ───────────────────────
python3 -c "
content = open('$FILE').read()
dna_view = '''
    <!-- ══ SKILL DNA VIEW ══════════════════════════════════════ -->
    <div x-show=\"view==='dna'\" style=\"display:none\" x-init=\"\$watch('view', v => { if(v==='dna') initDNA() })\">
      <div class=\"page-header\">
        <div>
          <div class=\"page-title\">Skill DNA</div>
          <div class=\"page-subtitle\">Your unique developer fingerprint — hover over nodes to explore skills</div>
        </div>
        <div style=\"display:flex;gap:9px;align-items:center\">
          <button class=\"btn btn-ghost btn-sm\" onclick=\"rotateDNA()\">
            <svg width=\"13\" height=\"13\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"><polyline points=\"23 4 23 10 17 10\"/><path d=\"M20.49 15a9 9 0 1 1-2.12-9.36L23 10\"/></svg>
            Auto-rotate
          </button>
          <button class=\"btn btn-primary btn-sm\" onclick=\"exportDNA()\">
            <svg width=\"13\" height=\"13\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"><path d=\"M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4\"/><polyline points=\"7 10 12 15 17 10\"/><line x1=\"12\" y1=\"15\" x2=\"12\" y2=\"3\"/></svg>
            Share DNA
          </button>
        </div>
      </div>

      <!-- Main DNA canvas + legend layout -->
      <div style=\"display:grid;grid-template-columns:1fr 280px;gap:16px;align-items:start\">

        <!-- 3D Canvas -->
        <div class=\"card\" style=\"padding:0;overflow:hidden;position:relative;height:600px;background:#0a0c14\">
          <canvas id=\"dna-canvas\" style=\"width:100%;height:100%;display:block\"></canvas>
          <!-- Hover tooltip -->
          <div id=\"dna-tooltip\" style=\"position:absolute;display:none;background:#161b27;border:1px solid #2a3147;border-radius:10px;padding:10px 14px;pointer-events:none;z-index:10;min-width:160px\">
            <div id=\"tt-skill\" style=\"font-weight:600;font-size:13px;margin-bottom:4px\"></div>
            <div style=\"display:flex;align-items:center;gap:8px;margin-bottom:6px\">
              <div id=\"tt-bar\" style=\"flex:1;height:4px;background:#2a3147;border-radius:2px\"><div id=\"tt-fill\" style=\"height:100%;border-radius:2px;transition:width .3s\"></div></div>
              <span id=\"tt-score\" style=\"font-family:DM Mono,monospace;font-size:11px;color:#7b82a0\"></span>
            </div>
            <div id=\"tt-category\" style=\"font-size:10px;color:#7b82a0\"></div>
            <div id=\"tt-decay\" style=\"font-size:10px;margin-top:4px\"></div>
          </div>
          <!-- Loading state -->
          <div id=\"dna-loading\" style=\"position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px\">
            <div class=\"spinner\" style=\"width:24px;height:24px;border-width:3px\"></div>
            <div style=\"font-size:12px;color:#7b82a0\">Sequencing your DNA...</div>
          </div>
        </div>

        <!-- Right panel -->
        <div style=\"display:flex;flex-direction:column;gap:12px\">

          <!-- DNA Stats card -->
          <div class=\"card\">
            <div class=\"card-header\"><div class=\"card-title\">DNA Profile</div></div>
            <div style=\"display:flex;flex-direction:column;gap:10px\" id=\"dna-stats\">
              <div style=\"display:flex;justify-content:space-between;align-items:center\">
                <span style=\"font-size:11px;color:#7b82a0\">Total Skills</span>
                <span id=\"dna-total\" style=\"font-family:DM Mono,monospace;font-size:13px;font-weight:600\">—</span>
              </div>
              <div style=\"display:flex;justify-content:space-between;align-items:center\">
                <span style=\"font-size:11px;color:#7b82a0\">Dominant Category</span>
                <span id=\"dna-dominant\" style=\"font-size:12px;font-weight:600\">—</span>
              </div>
              <div style=\"display:flex;justify-content:space-between;align-items:center\">
                <span style=\"font-size:11px;color:#7b82a0\">Helix Strength</span>
                <span id=\"dna-strength\" style=\"font-size:12px;font-weight:600;color:#10b981\">—</span>
              </div>
              <div style=\"display:flex;justify-content:space-between;align-items:center\">
                <span style=\"font-size:11px;color:#7b82a0\">DNA Hash</span>
                <span id=\"dna-hash\" style=\"font-family:DM Mono,monospace;font-size:10px;color:#6366f1\">—</span>
              </div>
            </div>
          </div>

          <!-- Legend -->
          <div class=\"card\">
            <div class=\"card-header\"><div class=\"card-title\">Categories</div></div>
            <div style=\"display:flex;flex-direction:column;gap:8px\">
              <div style=\"display:flex;align-items:center;gap:9px\">
                <div style=\"width:10px;height:10px;border-radius:50%;background:#6366f1;flex-shrink:0\"></div>
                <span style=\"font-size:11px;color:#e2e6f3\">Languages</span>
                <span style=\"font-size:10px;color:#7b82a0;margin-left:auto\">Python, JS, Go...</span>
              </div>
              <div style=\"display:flex;align-items:center;gap:9px\">
                <div style=\"width:10px;height:10px;border-radius:50%;background:#8b5cf6;flex-shrink:0\"></div>
                <span style=\"font-size:11px;color:#e2e6f3\">Frameworks</span>
                <span style=\"font-size:10px;color:#7b82a0;margin-left:auto\">React, FastAPI...</span>
              </div>
              <div style=\"display:flex;align-items:center;gap:9px\">
                <div style=\"width:10px;height:10px;border-radius:50%;background:#10b981;flex-shrink:0\"></div>
                <span style=\"font-size:11px;color:#e2e6f3\">Infrastructure</span>
                <span style=\"font-size:10px;color:#7b82a0;margin-left:auto\">Docker, K8s...</span>
              </div>
              <div style=\"display:flex;align-items:center;gap:9px\">
                <div style=\"width:10px;height:10px;border-radius:50%;background:#f59e0b;flex-shrink:0\"></div>
                <span style=\"font-size:11px;color:#e2e6f3\">Databases</span>
                <span style=\"font-size:10px;color:#7b82a0;margin-left:auto\">PostgreSQL, Redis...</span>
              </div>
              <div style=\"display:flex;align-items:center;gap:9px\">
                <div style=\"width:10px;height:10px;border-radius:50%;background:#06b6d4;flex-shrink:0\"></div>
                <span style=\"font-size:11px;color:#e2e6f3\">Other</span>
                <span style=\"font-size:10px;color:#7b82a0;margin-left:auto\">Design, Cloud...</span>
              </div>
            </div>
          </div>

          <!-- Node size legend -->
          <div class=\"card\">
            <div class=\"card-header\"><div class=\"card-title\">Node Size</div></div>
            <div style=\"display:flex;flex-direction:column;gap:8px\">
              <div style=\"display:flex;align-items:center;gap:10px\">
                <div style=\"width:16px;height:16px;border-radius:50%;background:#6366f1;opacity:.9\"></div>
                <span style=\"font-size:11px;color:#e2e6f3\">Expert (70%+)</span>
              </div>
              <div style=\"display:flex;align-items:center;gap:10px\">
                <div style=\"width:11px;height:11px;border-radius:50%;background:#6366f1;opacity:.7\"></div>
                <span style=\"font-size:11px;color:#e2e6f3\">Intermediate (40–70%)</span>
              </div>
              <div style=\"display:flex;align-items:center;gap:10px\">
                <div style=\"width:7px;height:7px;border-radius:50%;background:#6366f1;opacity:.5\"></div>
                <span style=\"font-size:11px;color:#e2e6f3\">Beginner (&lt;40%)</span>
              </div>
            </div>
          </div>

          <!-- Skill list -->
          <div class=\"card\" style=\"max-height:200px;overflow-y:auto\">
            <div class=\"card-header\"><div class=\"card-title\">Skill Nodes</div></div>
            <div id=\"dna-skill-list\" style=\"display:flex;flex-direction:column;gap:6px\">
              <div style=\"font-size:11px;color:#7b82a0\">Complete assessments to populate your DNA</div>
            </div>
          </div>

        </div>
      </div>

      <!-- Empty state -->
      <div x-show=\"!Object.keys(skillMatrix).length\" style=\"text-align:center;padding:60px 0\">
        <div style=\"font-size:40px;margin-bottom:12px\">🧬</div>
        <div style=\"font-size:16px;font-weight:600;margin-bottom:6px\">No DNA yet</div>
        <div style=\"font-size:12px;color:#7b82a0;margin-bottom:20px\">Complete skill assessments to generate your unique DNA strand</div>
        <button class=\"btn btn-primary\" @click=\"go(\\'assessment\\')\">Start Assessment →</button>
      </div>
    </div>
'''
anchor = '  </main>'
if anchor in content:
    content = content.replace(anchor, dna_view + '  </main>', 1)
    open('$FILE', 'w').write(content)
    print('✅ DNA view HTML added')
else:
    print('❌ </main> anchor not found')
    import sys; sys.exit(1)
"

# ── 3. Add go('dna') handler and Three.js DNA engine before closing </script> ─
python3 -c "
content = open('$FILE').read()

dna_js = '''
      if (newView === 'dna') setTimeout(() => initDNA(), 200);'''

# Patch go() function to trigger DNA init
old_go = \"      if (newView === 'room' && this.activeRoom) setTimeout(() => renderTeamRadar(this.activeRoom.team_matrix || {}), 200);\"
new_go = old_go + dna_js
if old_go in content:
    content = content.replace(old_go, new_go, 1)
    print('✅ go() handler patched')
else:
    print('⚠️  go() anchor not found, skipping')

open('$FILE', 'w').write(content)
"

# ── 4. Inject the Three.js DNA engine as a <script> block before </body> ──────
python3 << 'PYEOF'
content = open('frontend/index.html').read()

three_script = '''
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script>
// ════════════════════════════════════════════════════════════════════
// SKILL DNA — 3D Double Helix Visualizer (Three.js r128)
// ════════════════════════════════════════════════════════════════════

const DNA_CATEGORIES = {
  Python:'language', JavaScript:'language', TypeScript:'language',
  Go:'language', Rust:'language', Java:'language', 'C++':'language',
  Ruby:'language', PHP:'language', Swift:'language', Kotlin:'language',
  React:'framework', 'Node.js':'framework', FastAPI:'framework',
  Django:'framework', Vue:'framework', Angular:'framework',
  Spring:'framework', Rails:'framework', NextJS:'framework',
  PostgreSQL:'database', MySQL:'database', Redis:'database',
  MongoDB:'database', SQLite:'database', Cassandra:'database',
  Docker:'infra', Kubernetes:'infra', AWS:'infra', Terraform:'infra',
  Linux:'infra', Nginx:'infra', Git:'infra', 'System Design':'other',
  GraphQL:'other', 'Machine Learning':'other', CSS:'other',
};

const DNA_COLORS = {
  language:  0x6366f1,
  framework: 0x8b5cf6,
  database:  0xf59e0b,
  infra:     0x10b981,
  other:     0x06b6d4,
};

const DNA_COLOR_HEX = {
  language:  '#6366f1',
  framework: '#8b5cf6',
  database:  '#f59e0b',
  infra:     '#10b981',
  other:     '#06b6d4',
};

let _dnaScene, _dnaCamera, _dnaRenderer, _dnaAnimId, _dnaAutoRotate = true;
let _dnaSkillMeshes = [], _dnaMixer, _dnaSkillData = [];

function getCategoryLabel(cat) {
  return {language:'Language',framework:'Framework',database:'Database',infra:'Infrastructure',other:'Other'}[cat] || 'Other';
}

function getSkillCategory(name) {
  return DNA_CATEGORIES[name] || 'other';
}

function disposeDNA() {
  if (_dnaAnimId) cancelAnimationFrame(_dnaAnimId);
  if (_dnaRenderer) { _dnaRenderer.dispose(); _dnaRenderer.domElement.remove(); _dnaRenderer = null; }
  _dnaScene = null; _dnaCamera = null; _dnaSkillMeshes = []; _dnaSkillData = [];
}

function initDNA() {
  const alpineApp = document.querySelector('[x-data]').__x?.$data || Alpine.$data(document.querySelector('[x-data]'));
  const skillMatrix = alpineApp ? alpineApp.skillMatrix : {};

  if (!skillMatrix || !Object.keys(skillMatrix).length) return;

  disposeDNA();

  const canvas = document.getElementById('dna-canvas');
  const loading = document.getElementById('dna-loading');
  if (!canvas) return;

  const W = canvas.clientWidth || 700;
  const H = canvas.clientHeight || 600;

  // ── Scene setup ───────────────────────────────────────────────
  _dnaScene = new THREE.Scene();
  _dnaScene.background = new THREE.Color(0x0a0c14);
  _dnaScene.fog = new THREE.FogExp2(0x0a0c14, 0.035);

  _dnaCamera = new THREE.PerspectiveCamera(50, W / H, 0.1, 100);
  _dnaCamera.position.set(0, 0, 18);

  _dnaRenderer = new THREE.WebGLRenderer({ antialias: true });
  _dnaRenderer.setSize(W, H);
  _dnaRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  canvas.appendChild(_dnaRenderer.domElement);
  _dnaRenderer.domElement.style.width = '100%';
  _dnaRenderer.domElement.style.height = '100%';

  // ── Ambient + directional lights ──────────────────────────────
  _dnaScene.add(new THREE.AmbientLight(0xffffff, 0.4));
  const dirLight = new THREE.DirectionalLight(0x6366f1, 1.2);
  dirLight.position.set(5, 10, 5);
  _dnaScene.add(dirLight);
  const dirLight2 = new THREE.DirectionalLight(0x8b5cf6, 0.8);
  dirLight2.position.set(-5, -5, 5);
  _dnaScene.add(dirLight2);

  // ── Build the helix ───────────────────────────────────────────
  const skills = Object.entries(skillMatrix).sort((a,b) => b[1]-a[1]);
  const N = skills.length;
  const HELIX_RADIUS = 3.5;
  const HELIX_HEIGHT = 14;
  const TURNS = 2.5;

  _dnaSkillData = [];

  // Backbone tubes — two strands
  const backbonePoints1 = [], backbonePoints2 = [];
  const BACKBONE_STEPS = 80;
  for (let i = 0; i <= BACKBONE_STEPS; i++) {
    const t = i / BACKBONE_STEPS;
    const angle = t * Math.PI * 2 * TURNS;
    const y = (t - 0.5) * HELIX_HEIGHT;
    backbonePoints1.push(new THREE.Vector3(Math.cos(angle) * HELIX_RADIUS, y, Math.sin(angle) * HELIX_RADIUS));
    backbonePoints2.push(new THREE.Vector3(Math.cos(angle + Math.PI) * HELIX_RADIUS, y, Math.sin(angle + Math.PI) * HELIX_RADIUS));
  }

  const makeTube = (pts, color) => {
    const curve = new THREE.CatmullRomCurve3(pts);
    const geo = new THREE.TubeGeometry(curve, 80, 0.08, 8, false);
    const mat = new THREE.MeshStandardMaterial({ color, transparent: true, opacity: 0.35, roughness: 0.3, metalness: 0.6 });
    return new THREE.Mesh(geo, mat);
  };
  _dnaScene.add(makeTube(backbonePoints1, 0x6366f1));
  _dnaScene.add(makeTube(backbonePoints2, 0x8b5cf6));

  // Skill nodes + rungs
  skills.forEach(([skill, score], i) => {
    const t = (i + 0.5) / N;
    const angle = t * Math.PI * 2 * TURNS;
    const y = (t - 0.5) * HELIX_HEIGHT;

    const cat = getSkillCategory(skill);
    const color = DNA_COLORS[cat];

    // Node size based on score
    const radius = 0.15 + score * 0.45;

    // Position on strand 1
    const x1 = Math.cos(angle) * HELIX_RADIUS;
    const z1 = Math.sin(angle) * HELIX_RADIUS;
    const x2 = Math.cos(angle + Math.PI) * HELIX_RADIUS;
    const z2 = Math.sin(angle + Math.PI) * HELIX_RADIUS;

    // Skill node (sphere) on strand 1
    const geo = new THREE.SphereGeometry(radius, 16, 16);
    const mat = new THREE.MeshStandardMaterial({
      color,
      emissive: color,
      emissiveIntensity: 0.3 + score * 0.5,
      roughness: 0.2,
      metalness: 0.5,
      transparent: true,
      opacity: 0.5 + score * 0.5,
    });
    const sphere = new THREE.Mesh(geo, mat);
    sphere.position.set(x1, y, z1);
    sphere.userData = { skill, score, cat, color: DNA_COLOR_HEX[cat] };
    _dnaScene.add(sphere);
    _dnaSkillMeshes.push(sphere);
    _dnaSkillData.push({ skill, score, cat, x: x1, y, z: z1 });

    // Mirror node on strand 2 (smaller, dimmer)
    const geo2 = new THREE.SphereGeometry(radius * 0.5, 12, 12);
    const mat2 = new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.1, roughness: 0.4, metalness: 0.3, transparent: true, opacity: 0.3 });
    const sphere2 = new THREE.Mesh(geo2, mat2);
    sphere2.position.set(x2, y, z2);
    _dnaScene.add(sphere2);

    // Rung connecting the two strands
    const rungGeo = new THREE.CylinderGeometry(0.03, 0.03, Math.sqrt((x2-x1)**2+(z2-z1)**2), 6);
    const rungMat = new THREE.MeshStandardMaterial({ color, transparent: true, opacity: 0.2 });
    const rung = new THREE.Mesh(rungGeo, rungMat);
    rung.position.set((x1+x2)/2, y, (z1+z2)/2);
    rung.lookAt(x2, y, z2);
    rung.rotateX(Math.PI / 2);
    _dnaScene.add(rung);
  });

  // Particle field (background stars)
  const starsGeo = new THREE.BufferGeometry();
  const starPositions = new Float32Array(300 * 3);
  for (let i = 0; i < 300; i++) {
    starPositions[i*3]   = (Math.random() - 0.5) * 40;
    starPositions[i*3+1] = (Math.random() - 0.5) * 30;
    starPositions[i*3+2] = (Math.random() - 0.5) * 20 - 10;
  }
  starsGeo.setAttribute('position', new THREE.BufferAttribute(starPositions, 3));
  const starsMat = new THREE.PointsMaterial({ color: 0x2a3147, size: 0.08 });
  _dnaScene.add(new THREE.Points(starsGeo, starsMat));

  // ── Mouse interaction ─────────────────────────────────────────
  const raycaster = new THREE.Raycaster();
  const mouse = new THREE.Vector2(-9999, -9999);
  const tooltip = document.getElementById('dna-tooltip');

  const canvasEl = _dnaRenderer.domElement;
  canvasEl.addEventListener('mousemove', (e) => {
    const rect = canvasEl.getBoundingClientRect();
    mouse.x =  ((e.clientX - rect.left) / rect.width)  * 2 - 1;
    mouse.y = -((e.clientY - rect.top)  / rect.height) * 2 + 1;

    raycaster.setFromCamera(mouse, _dnaCamera);
    const hits = raycaster.intersectObjects(_dnaSkillMeshes);

    if (hits.length > 0) {
      const mesh = hits[0].object;
      const d = mesh.userData;
      document.getElementById('tt-skill').textContent = d.skill;
      document.getElementById('tt-score').textContent = Math.round(d.score * 100) + '%';
      document.getElementById('tt-fill').style.width = Math.round(d.score * 100) + '%';
      document.getElementById('tt-fill').style.background = d.color;
      document.getElementById('tt-category').textContent = getCategoryLabel(d.cat);
      // Decay indicator
      const pct = Math.round(d.score * 100);
      const decayEl = document.getElementById('tt-decay');
      if (pct >= 70) decayEl.innerHTML = '<span style="color:#10b981">✅ Strong — keep it up</span>';
      else if (pct >= 40) decayEl.innerHTML = '<span style="color:#f59e0b">⚠️ Developing — practice soon</span>';
      else decayEl.innerHTML = '<span style="color:#ef4444">🔴 Weak — needs attention</span>';

      tooltip.style.display = 'block';
      tooltip.style.left = (e.clientX - rect.left + 14) + 'px';
      tooltip.style.top  = (e.clientY - rect.top  - 10) + 'px';
      canvasEl.style.cursor = 'pointer';

      // Pulse hovered node
      mesh.material.emissiveIntensity = 1.0;
    } else {
      tooltip.style.display = 'none';
      canvasEl.style.cursor = 'default';
      _dnaSkillMeshes.forEach(m => { m.material.emissiveIntensity = 0.3 + (m.userData.score||0) * 0.5; });
    }
  });

  canvasEl.addEventListener('mouseleave', () => { tooltip.style.display = 'none'; });

  // ── Drag to rotate ────────────────────────────────────────────
  let isDragging = false, prevX = 0, prevY = 0;
  const dnaGroup = new THREE.Group();
  // Move all objects into group
  while (_dnaScene.children.length > 3) { // keep lights + stars
    // We just rotate the scene instead
  }

  canvasEl.addEventListener('mousedown', (e) => { isDragging = true; prevX = e.clientX; prevY = e.clientY; _dnaAutoRotate = false; });
  window.addEventListener('mouseup', () => { isDragging = false; });
  canvasEl.addEventListener('mousemove', (e) => {
    if (!isDragging) return;
    const dx = e.clientX - prevX;
    const dy = e.clientY - prevY;
    _dnaScene.rotation.y += dx * 0.008;
    _dnaScene.rotation.x += dy * 0.004;
    prevX = e.clientX; prevY = e.clientY;
  });

  // Scroll to zoom
  canvasEl.addEventListener('wheel', (e) => {
    _dnaCamera.position.z = Math.max(8, Math.min(30, _dnaCamera.position.z + e.deltaY * 0.02));
  });

  // ── Update stats panel ────────────────────────────────────────
  if (loading) loading.style.display = 'none';

  const total = skills.length;
  document.getElementById('dna-total').textContent = total + ' skills';

  const catCounts = {};
  skills.forEach(([s]) => { const c = getSkillCategory(s); catCounts[c] = (catCounts[c]||0)+1; });
  const dominant = Object.entries(catCounts).sort((a,b)=>b[1]-a[1])[0];
  document.getElementById('dna-dominant').textContent = dominant ? getCategoryLabel(dominant[0]) : '—';
  document.getElementById('dna-dominant').style.color = dominant ? DNA_COLOR_HEX[dominant[0]] : '#7b82a0';

  const avgScore = skills.reduce((s,[,v])=>s+v,0)/total;
  const strength = avgScore >= 0.7 ? '💪 Strong' : avgScore >= 0.4 ? '🌱 Growing' : '🔰 Beginner';
  document.getElementById('dna-strength').textContent = strength;

  // DNA hash — unique fingerprint
  const hash = skills.slice(0,6).map(([s,v])=>s[0]+Math.round(v*9)).join('');
  document.getElementById('dna-hash').textContent = hash.toUpperCase() || '——';

  // Skill list
  const listEl = document.getElementById('dna-skill-list');
  listEl.innerHTML = skills.map(([s,v]) => {
    const cat = getSkillCategory(s);
    const color = DNA_COLOR_HEX[cat];
    return \`<div style="display:flex;align-items:center;gap:8px;padding:3px 0">
      <div style="width:7px;height:7px;border-radius:50%;background:\${color};flex-shrink:0"></div>
      <span style="font-size:11px;flex:1">\${s}</span>
      <span style="font-family:DM Mono,monospace;font-size:10px;color:#7b82a0">\${Math.round(v*100)}%</span>
    </div>\`;
  }).join('');

  // ── Animation loop ────────────────────────────────────────────
  const clock = new THREE.Clock();
  function animate() {
    _dnaAnimId = requestAnimationFrame(animate);
    const t = clock.getElapsedTime();

    if (_dnaAutoRotate) {
      _dnaScene.rotation.y += 0.004;
    }

    // Pulsing glow on nodes
    _dnaSkillMeshes.forEach((mesh, i) => {
      const base = 0.3 + (mesh.userData.score||0) * 0.4;
      mesh.material.emissiveIntensity = base + Math.sin(t * 1.5 + i * 0.4) * 0.15;
    });

    _dnaRenderer.render(_dnaScene, _dnaCamera);
  }
  animate();

  // Handle resize
  const resizeObs = new ResizeObserver(() => {
    if (!_dnaRenderer) return;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    _dnaCamera.aspect = w / h;
    _dnaCamera.updateProjectionMatrix();
    _dnaRenderer.setSize(w, h);
  });
  resizeObs.observe(canvas);
}

function rotateDNA() {
  _dnaAutoRotate = !_dnaAutoRotate;
}

function exportDNA() {
  const alpineApp = Alpine.$data(document.querySelector('[x-data]'));
  const skills = Object.entries(alpineApp.skillMatrix || {}).sort((a,b)=>b[1]-a[1]);
  const hash = skills.slice(0,6).map(([s,v])=>s[0]+Math.round(v*9)).join('').toUpperCase();
  const text = \`🧬 My Skill DNA on SkillForge\\n\\nHash: \${hash}\\n\\n\` +
    skills.map(([s,v]) => \`\${s}: \${Math.round(v*100)}%\`).join('\\n') +
    \`\\n\\nGenerated by SkillForge — AI-powered skill assessment\`;
  navigator.clipboard?.writeText(text);
  toast('DNA copied to clipboard! Share it anywhere 🧬', 'success');
}
</script>
'''

if '</body>' in content:
    content = content.replace('</body>', three_script + '</body>', 1)
    open('frontend/index.html', 'w').write(content)
    print('✅ Three.js DNA engine injected')
else:
    print('❌ </body> not found')
    import sys; sys.exit(1)
PYEOF

echo ""
echo "═══════════════════════════════════════"
echo "  🧬 Skill DNA page added successfully!"
echo "  Hard refresh browser: Cmd+Shift+R"
echo "═══════════════════════════════════════"
