import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { ProceduralAudioManager } from './audio.js';

let scene, camera, renderer, model;
let currentStage = 0;
let isLocked = false;
let stages = [];
const audio = new ProceduralAudioManager();

const container = document.getElementById('webgl-container');
const fallback = document.getElementById('fallback-poster');
const headlineEl = document.getElementById('stage-headline');
const descEl = document.getElementById('stage-description');
const numberEl = document.getElementById('stage-number');
const dots = document.querySelectorAll('.view-dot');
const soundBtn = document.getElementById('sound-btn');
const themeToggleBtn = document.getElementById('theme-toggle');

const DEFAULT_STAGES = [
  {
    stage: 1,
    fov: 36,
    camera: { x: 5.0, y: 3.5, z: 6.5 },
    target: { x: 0.0, y: 1.0, z: 0.0 },
    headline: "Enchanted Farmstead Sanctuary",
    description: "An intimate botanical hideaway in Kalena Agrahara. Quiet gravel pathways, canopy trees, and bespoke European degustation."
  },
  {
    stage: 2,
    fov: 32,
    camera: { x: 2.8, y: 2.0, z: 4.5 },
    target: { x: 0.0, y: 1.0, z: 0.0 },
    headline: "Seven-Course Culinary Artistry",
    description: "Slow-cooked, unhurried multi-course dining by Sonali Sattar & Himanshu Dimri. Honest organic ingredients transformed with European precision."
  },
  {
    stage: 3,
    fov: 34,
    camera: { x: -3.5, y: 2.5, z: 5.0 },
    target: { x: -0.5, y: 1.0, z: 0.0 },
    headline: "Under The Star Canopy",
    description: "Handcrafted ironwork tables secluded amidst verdant foliage. Calibrated ambient candlelight and absolute privacy."
  },
  {
    stage: 4,
    fov: 38,
    camera: { x: 1.2, y: 1.8, z: 3.8 },
    target: { x: 0.0, y: 1.0, z: 0.0 },
    headline: "Exclusively By Prior Reservation",
    description: "Zero walk-ins. Every table prepared hours in advance specifically for your party."
  },
  {
    stage: 5,
    fov: 30,
    camera: { x: -3.8, y: 2.2, z: 5.8 },
    target: { x: 1.0, y: 1.0, z: 0.0 },
    headline: "Direct Reservation Concierge",
    description: "Connect directly with our hospitality desk to curate dietary preferences and reserve your evening."
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
    scene.background = null;

    const s0 = stages[0] || DEFAULT_STAGES[0];
    camera = new THREE.PerspectiveCamera(s0.fov || 35, window.innerWidth / window.innerHeight, 0.1, 100);
    camera.position.set(s0.camera.x, s0.camera.y, s0.camera.z);
    camera.lookAt(s0.target.x, s0.target.y, s0.target.z);

    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2.0);
    renderer.setPixelRatio(pixelRatio);
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.15;

    container.appendChild(renderer.domElement);

    // Warm ambient lighting suitable for fine dining / foliage
    const ambientLight = new THREE.AmbientLight(0xfffaed, 0.9);
    scene.add(ambientLight);

    const dirLight1 = new THREE.DirectionalLight(0xfffaed, 1.8);
    dirLight1.position.set(6, 10, 6);
    scene.add(dirLight1);

    const dirLight2 = new THREE.DirectionalLight(0xd4af37, 1.0);
    dirLight2.position.set(-6, 4, -4);
    scene.add(dirLight2);

    // Try loading verified GLB model, else fallback to procedural botanical showcase
    const loader = new GLTFLoader();
    loader.load('./assets/model.glb', (gltf) => {
      model = gltf.scene;
      // Keep the sculpture clear of the hero text column (left) on desktop;
      // stack it above the copy on narrow viewports.
      const onMobile = window.innerWidth < 768;
      model.position.set(onMobile ? 0 : 2.1, onMobile ? 2.4 : 0.3, 0);
      model.scale.setScalar(onMobile ? 0.85 : 1.2);
      scene.add(model);
    }, undefined, () => {
      // Procedural sculpture fallback
      const geometry = new THREE.DodecahedronGeometry(1.6, 1);
      const material = new THREE.MeshStandardMaterial({
        color: 0x2b382b,
        metalness: 0.3,
        roughness: 0.4,
        emissive: 0x0f180f
      });
      model = new THREE.Mesh(geometry, material);
      model.position.set(0, 1, 0);
      scene.add(model);
    });

    window.addEventListener('resize', onWindowResize);
    setupInteraction();
    animate();
  } catch (err) {
    console.error("WebGL initialization failed.", err);
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

  const startPos = { ...camera.position };
  const targetPos = s.camera;
  const duration = 1200;
  const startTime = performance.now();

  try { audio.playCameraWhoosh(); } catch(e) {}

  if (numberEl) numberEl.textContent = `0${s.stage} / PERSPECTIVE`;
  if (headlineEl) headlineEl.textContent = s.headline;
  if (descEl) descEl.textContent = s.description;

  dots.forEach((dot, idx) => {
    dot.classList.toggle('active', idx === currentStage);
  });

  function step(now) {
    const elapsed = now - startTime;
    const progress = Math.min(1.0, elapsed / duration);
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
  if (soundBtn) {
    soundBtn.addEventListener('click', () => {
      const active = audio.toggle();
      soundBtn.textContent = active ? 'Sound: On' : 'Sound: Off';
    });
  }

  if (themeToggleBtn) {
    themeToggleBtn.addEventListener('click', () => {
      const isDark = document.documentElement.classList.toggle('dark');
      localStorage.setItem('theme', isDark ? 'dark' : 'light');
      themeToggleBtn.textContent = isDark ? '☀ Light' : '☾ Dark';
    });
    // Set initial text
    const saved = localStorage.getItem('theme');
    if (saved === 'dark') {
      document.documentElement.classList.add('dark');
      themeToggleBtn.textContent = '☀ Light';
    } else {
      document.documentElement.classList.remove('dark');
      themeToggleBtn.textContent = '☾ Dark';
    }
  }

  dots.forEach(dot => {
    dot.addEventListener('click', (e) => {
      const idx = parseInt(e.target.dataset.index, 10);
      if (!isLocked && idx !== currentStage) {
        updateStage(idx);
      }
    });
  });

  const prevBtn = document.getElementById('prev-stage-btn');
  const nextBtn = document.getElementById('next-stage-btn');
  if (prevBtn) {
    prevBtn.addEventListener('click', () => {
      if (!isLocked && currentStage > 0) updateStage(currentStage - 1);
    });
  }
  if (nextBtn) {
    nextBtn.addEventListener('click', () => {
      if (!isLocked && currentStage < stages.length - 1) updateStage(currentStage + 1);
    });
  }
}

function animate() {
  requestAnimationFrame(animate);
  if (model) {
    model.rotation.y += 0.0015;
  }
  if (renderer && scene && camera) {
    renderer.render(scene, camera);
  }
}

loadStages().then(() => {
  initWebGL();
});
