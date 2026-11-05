import * as THREE from 'three';
import { ProceduralAudioManager } from './audio.js';

// Setup state
let scene, camera, renderer, model;
let currentStage = 0;
let isLocked = false;
let stages = [];
const audio = new ProceduralAudioManager();

// Elements
const container = document.getElementById('webgl-container');
const fallback = document.getElementById('fallback-poster');
const headlineEl = document.getElementById('stage-headline');
const descEl = document.getElementById('stage-description');
const numberEl = document.getElementById('stage-number');
const dots = document.querySelectorAll('.view-dot');
const soundBtn = document.getElementById('sound-btn');

// Default fallback stages if stages.json is not loaded
const DEFAULT_STAGES = [
  {
    stage: 1,
    camera: { x: 5.0, y: 3.0, z: 6.0 },
    target: { x: 0.0, y: 1.0, z: 0.0 },
    fov: 35,
    headline: "Form & Proportion",
    description: "Architectural purity defined by restrained material balance and natural illumination."
  },
  {
    stage: 2,
    camera: { x: 2.5, y: 1.8, z: 4.0 },
    target: { x: 0.0, y: 1.0, z: 0.0 },
    fov: 30,
    headline: "Precision Tactility",
    description: "Sub-millimeter joints and PBR material reflections reacting dynamically to ambient light."
  },
  {
    stage: 3,
    camera: { x: -3.5, y: 2.2, z: 4.5 },
    target: { x: -0.5, y: 1.2, z: 0.0 },
    fov: 32,
    headline: "Sculptural Volume",
    description: "Cantilevered planes and geometric intersections creating calibrated shadow depth."
  },
  {
    stage: 4,
    camera: { x: 0.0, y: 4.5, z: 3.5 },
    target: { x: 0.0, y: 0.8, z: 0.0 },
    fov: 38,
    headline: "Spatial Continuity",
    description: "Seamless transitions between structural envelope and interior threshold."
  },
  {
    stage: 5,
    camera: { x: -4.5, y: 1.8, z: 5.5 },
    target: { x: 1.5, y: 1.0, z: 0.0 },
    fov: 28,
    headline: "Enduring Legacy",
    description: "Bespoke engineering crafted for permanence and uncompromising luxury standards."
  }
];

async function loadStages() {
  try {
    const res = await fetch('./stages.json');
    if (res.ok) {
      stages = await res.json();
    } else {
      stages = DEFAULT_STAGES;
    }
  } catch (e) {
    stages = DEFAULT_STAGES;
  }
}

function initWebGL() {
  try {
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0a0c);

    const s0 = stages[0] || DEFAULT_STAGES[0];
    camera = new THREE.PerspectiveCamera(s0.fov || 35, window.innerWidth / window.innerHeight, 0.1, 100);
    camera.position.set(s0.camera.x, s0.camera.y, s0.camera.z);
    camera.lookAt(s0.target.x, s0.target.y, s0.target.z);

    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2.0);
    renderer.setPixelRatio(pixelRatio);
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.1;

    container.appendChild(renderer.domElement);

    // Setup Lighting
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
    scene.add(ambientLight);

    const dirLight1 = new THREE.DirectionalLight(0xffffff, 1.8);
    dirLight1.position.set(8, 12, 6);
    scene.add(dirLight1);

    const dirLight2 = new THREE.DirectionalLight(0xd4af37, 0.8);
    dirLight2.position.set(-6, 4, -4);
    scene.add(dirLight2);

    // Placeholder Geometric Showcase Object
    const geometry = new THREE.BoxGeometry(2, 2, 2);
    const material = new THREE.MeshStandardMaterial({
      color: 0x222226,
      metalness: 0.85,
      roughness: 0.25,
      emissive: 0x111115
    });
    model = new THREE.Mesh(geometry, material);
    model.position.set(0, 1, 0);
    scene.add(model);

    // Wireframe overlay for hover effect
    const wireGeo = new THREE.WireframeGeometry(geometry);
    const wireMat = new THREE.LineBasicMaterial({ color: 0xd4af37, transparent: true, opacity: 0.0 });
    const wireframe = new THREE.LineSegments(wireGeo, wireMat);
    model.add(wireframe);

    // Hover wireframe listener
    container.addEventListener('pointerenter', () => {
      wireMat.opacity = 0.6;
      audio.playHoverBlip();
    });
    container.addEventListener('pointerleave', () => {
      wireMat.opacity = 0.0;
    });

    window.addEventListener('resize', onWindowResize);
    setupInteraction();
    animate();
  } catch (err) {
    console.error("WebGL initialization failed. Activating high-res fallback poster.", err);
    if (container) container.style.display = 'none';
    if (fallback) fallback.style.display = 'block';
  }
}

function onWindowResize() {
  if (!camera || !renderer) return;
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}

function updateStage(index) {
  if (index < 0 || index >= stages.length) return;
  currentStage = index;
  const s = stages[index];

  if (s.fov && s.fov !== camera.fov) {
    camera.fov = s.fov;
    camera.updateProjectionMatrix();
  }

  // Animate camera target position
  const startPos = { ...camera.position };
  const targetPos = s.camera;
  const duration = 1200; // ms
  const startTime = performance.now();

  audio.playCameraWhoosh();

  // Update UI overlay
  if (numberEl) numberEl.textContent = `0${s.stage} / STAGE`;
  if (headlineEl) headlineEl.textContent = s.headline;
  if (descEl) descEl.textContent = s.description;

  dots.forEach((dot, idx) => {
    dot.classList.toggle('active', idx === currentStage);
  });

  function step(now) {
    const elapsed = now - startTime;
    const progress = Math.min(1.0, elapsed / duration);
    // Smooth ease-out cubic
    const ease = 1 - Math.pow(1 - progress, 3);

    camera.position.x = startPos.x + (targetPos.x - startPos.x) * ease;
    camera.position.y = startPos.y + (targetPos.y - startPos.y) * ease;
    camera.position.z = startPos.z + (targetPos.z - startPos.z) * ease;
    camera.lookAt(s.target.x, s.target.y, s.target.z);

    if (progress < 1.0) {
      requestAnimationFrame(step);
    } else {
      isLocked = false;
    }
  }
  isLocked = true;
  requestAnimationFrame(step);
}

function setupInteraction() {
  // Sound button
  if (soundBtn) {
    soundBtn.addEventListener('click', () => {
      const active = audio.toggle();
      soundBtn.textContent = active ? 'Sound: On' : 'Sound: Off';
    });
  }

  // Dots click
  dots.forEach(dot => {
    dot.addEventListener('click', (e) => {
      const idx = parseInt(e.target.dataset.index, 10);
      if (!isLocked && idx !== currentStage) {
        updateStage(idx);
      }
    });
  });

  // Wheel gesture with cooldown & locking
  window.addEventListener('wheel', (e) => {
    if (isLocked) return;
    if (Math.abs(e.deltaY) > 20) {
      if (e.deltaY > 0 && currentStage < stages.length - 1) {
        updateStage(currentStage + 1);
      } else if (e.deltaY < 0 && currentStage > 0) {
        updateStage(currentStage - 1);
      }
    }
  }, { passive: true });

  // Keyboard navigation
  window.addEventListener('keydown', (e) => {
    if (isLocked) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight' || e.key === 'PageDown') {
      if (currentStage < stages.length - 1) updateStage(currentStage + 1);
    } else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft' || e.key === 'PageUp') {
      if (currentStage > 0) updateStage(currentStage - 1);
    }
  });
}

function animate() {
  requestAnimationFrame(animate);
  if (model) {
    model.rotation.y += 0.002;
  }
  if (renderer && scene && camera) {
    renderer.render(scene, camera);
  }
}

// Start
loadStages().then(() => {
  initWebGL();
});
