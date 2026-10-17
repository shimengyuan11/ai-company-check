/* ============================================================================
   登录页 · 蓝色地球
   ----------------------------------------------------------------------------
   纯 three.js（vendor 在 /static/vendor，无 CDN 依赖）。
   交互：拖拽旋转 / 滚轮缩放 / 点击地球发出涟漪脉冲；空闲 6 秒后恢复自转。
   兜底：WebGL 或纹理失败时保持原地看不见，页面回落到 CSS 同心光环
        （见 style.css 的 .auth-orbit），不会留一块空白。
   ========================================================================== */
import * as THREE from 'three';
import { OrbitControls } from 'orbit-controls';

const DEG = Math.PI / 180;
const R = 10;                       // 地球半径（世界单位，只用于取比例）
const host = document.getElementById('globe');

if (host) {
  try {
    start(host);
  } catch (err) {
    /* 保持 fail-soft：光环还在，只是没有 3D */
    host.dataset.state = 'failed';
    console.warn('[globe] 3D 地球初始化失败，回落到 CSS 光环：', err);
  }
}

function start(host) {
  const size = () => Math.max(1, host.clientWidth);

  /* ---- 渲染器 ---------------------------------------------------------- */
  const renderer = new THREE.WebGLRenderer({
    antialias: true,
    alpha: true,                    // 透出页面的深蓝底，地球像是浮在上面
    powerPreference: 'high-performance',
    /* 保留绘制缓冲：截图/右键存图能拿到真实画面，无头浏览器校验也靠它。
       登录页只有一个静态球，这点开销可以忽略。 */
    preserveDrawingBuffer: true
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(size(), size(), false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  host.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  /* fov 30 + 距离 38：球（直径 20）占画布约 92% —— 一打开就是主视觉。
     轨道环贴着球缘走，超出画幅的那一点点被裁掉是正常的，球体本身完整。 */
  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 400);
  camera.position.set(0, 4.5, 38);

  /* ---- 灯光：主光偏左上，让右侧自然进入夜面，城市灯才亮得出来 ------- */
  const keyLight = new THREE.DirectionalLight(0xffffff, 2.7);
  keyLight.position.set(-22, 14, 20);
  scene.add(keyLight);

  // 冷蓝补光，勾住球体下缘，避免夜面糊成一团黑
  const rimLight = new THREE.DirectionalLight(0x4b8cff, 1.1);
  rimLight.position.set(18, -10, -16);
  scene.add(rimLight);

  scene.add(new THREE.HemisphereLight(0x2a4a7a, 0x04060c, 0.55));

  /* ---- 地球本体 -------------------------------------------------------- */
  const loader = new THREE.TextureLoader();
  const dayTex = loader.load('/static/textures/earth-day.jpg');
  const nightTex = loader.load('/static/textures/earth-night.png');
  dayTex.colorSpace = THREE.SRGBColorSpace;
  nightTex.colorSpace = THREE.SRGBColorSpace;
  dayTex.anisotropy = renderer.capabilities.getMaxAnisotropy();

  const earthGroup = new THREE.Group();
  scene.add(earthGroup);

  const earth = new THREE.Mesh(
    new THREE.SphereGeometry(R, 96, 96),
    new THREE.MeshStandardMaterial({
      map: dayTex,
      emissiveMap: nightTex,
      emissive: new THREE.Color(0xffe3b8),   // 城市灯是暖的，和蓝色地球形成对照
      emissiveIntensity: 0.9,
      color: 0xdfe9ff,                        // 整体压冷，靠近品牌的蓝
      roughness: 0.94,
      metalness: 0.0
    })
  );
  earthGroup.add(earth);

  /* 大气边缘辉光：正面 + additive，边缘 fresnel 亮、中心透明 */
  const atmosphere = new THREE.Mesh(
    new THREE.SphereGeometry(R * 1.035, 96, 96),
    new THREE.ShaderMaterial({
      uniforms: {
        uColor: { value: new THREE.Color(0x4fa3ff) },
        uPower: { value: 2.6 },
        uStrength: { value: 1.05 }
      },
      vertexShader: /* glsl */`
        varying vec3 vN;
        varying vec3 vView;
        void main() {
          vN = normalize(normalMatrix * normal);
          vec4 mv = modelViewMatrix * vec4(position, 1.0);
          vView = -mv.xyz;
          gl_Position = projectionMatrix * mv;
        }
      `,
      fragmentShader: /* glsl */`
        uniform vec3 uColor;
        uniform float uPower;
        uniform float uStrength;
        varying vec3 vN;
        varying vec3 vView;
        void main() {
          float rim = pow(1.0 - max(dot(normalize(vN), normalize(vView)), 0.0), uPower);
          gl_FragColor = vec4(uColor, 1.0) * rim * uStrength;
        }
      `,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false
    })
  );
  earthGroup.add(atmosphere);

  /* 外围更淡的一层，营造"大气厚度" */
  const halo = new THREE.Mesh(
    new THREE.SphereGeometry(R * 1.14, 64, 64),
    new THREE.ShaderMaterial({
      uniforms: { uColor: { value: new THREE.Color(0x2f6fd0) } },
      vertexShader: /* glsl */`
        varying vec3 vN;
        varying vec3 vView;
        void main() {
          vN = normalize(normalMatrix * normal);
          vec4 mv = modelViewMatrix * vec4(position, 1.0);
          vView = -mv.xyz;
          gl_Position = projectionMatrix * mv;
        }
      `,
      fragmentShader: /* glsl */`
        uniform vec3 uColor;
        varying vec3 vN;
        varying vec3 vView;
        void main() {
          float rim = pow(1.0 - max(dot(normalize(vN), normalize(vView)), 0.0), 2.2);
          gl_FragColor = vec4(uColor, 1.0) * rim * 0.42;
        }
      `,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false
    })
  );
  earthGroup.add(halo);

  /* ---- 经纬网：像仪表盘刻度，给"高级"一点点秩序感 ------------------- */
  const gridMat = new THREE.LineBasicMaterial({
    color: 0x74bcff, transparent: true, opacity: 0.18,
    blending: THREE.AdditiveBlending, depthWrite: false
  });
  const gridPts = [];
  const rr = R * 1.002;
  for (let lon = 0; lon < 360; lon += 15) {
    for (let lat = -90; lat < 90; lat += 3) {
      const p1 = latLon(lat, lon, rr), p2 = latLon(lat + 3, lon, rr);
      gridPts.push(p1.x, p1.y, p1.z, p2.x, p2.y, p2.z);
    }
  }
  for (let lat = -75; lat <= 75; lat += 15) {
    for (let lon = 0; lon < 360; lon += 4) {
      const p1 = latLon(lat, lon, rr), p2 = latLon(lat, lon + 4, rr);
      gridPts.push(p1.x, p1.y, p1.z, p2.x, p2.y, p2.z);
    }
  }
  const gridGeo = new THREE.BufferGeometry();
  gridGeo.setAttribute('position', new THREE.Float32BufferAttribute(gridPts, 3));
  earthGroup.add(new THREE.LineSegments(gridGeo, gridMat));

  /* ---- 轨道环：两条细环 + 沿线游走的光点 ----------------------------- */
  const ringMatA = new THREE.MeshBasicMaterial({
    color: 0x6fb7ff, transparent: true, opacity: 0.24,
    blending: THREE.AdditiveBlending, depthWrite: false
  });
  const ringMatB = new THREE.MeshBasicMaterial({
    color: 0x3f7fdd, transparent: true, opacity: 0.15,
    blending: THREE.AdditiveBlending, depthWrite: false
  });

  const orbitA = new THREE.Group();
  const RING_A = R * 1.06;
  const ring1 = new THREE.Mesh(new THREE.TorusGeometry(RING_A, 0.014, 8, 240), ringMatA);
  ring1.rotation.x = Math.PI / 2;
  orbitA.add(ring1);
  orbitA.rotation.x = 0.38;
  earthGroup.add(orbitA);

  const orbitB = new THREE.Group();
  const ring2 = new THREE.Mesh(new THREE.TorusGeometry(R * 1.14, 0.010, 8, 240), ringMatB);
  ring2.rotation.x = Math.PI / 2;
  orbitB.add(ring2);
  orbitB.rotation.set(-0.55, 0, 0.85);
  earthGroup.add(orbitB);

  const dotTex = glowTexture();
  const satellite = new THREE.Sprite(new THREE.SpriteMaterial({
    map: dotTex, color: 0x9fd4ff, transparent: true,
    blending: THREE.AdditiveBlending, depthWrite: false
  }));
  satellite.scale.setScalar(0.9);
  orbitA.add(satellite);
  satellite.position.set(RING_A, 0, 0);

  /* ---- 航线：外贸的连接感。华南 / 华东发出，跨到各大洲 --------------- */
  const ROUTES = [
    [29.31, 120.07, 25.20, 55.27],     // 义乌 → 迪拜
    [23.13, 113.26, 53.55, 9.99],      // 广州 → 汉堡
    [22.54, 114.06, 34.05, -118.24],   // 深圳 → 洛杉矶
    [29.31, 120.07, -23.55, -46.63],   // 义乌 → 圣保罗
    [23.13, 113.26, 6.52, 3.38],       // 广州 → 拉各斯
    [31.23, 121.47, 1.35, 103.82]      // 上海 → 新加坡
  ];

  const routeMat = new THREE.LineBasicMaterial({
    color: 0x7ec8ff, transparent: true, opacity: 0.5,
    blending: THREE.AdditiveBlending, depthWrite: false
  });

  const routePoints = [];   // { curve, sprite, t, speed }
  ROUTES.forEach(([la1, lo1, la2, lo2], i) => {
    const a = latLon(la1, lo1, R);
    const b = latLon(la2, lo2, R);
    const mid = a.clone().add(b).multiplyScalar(0.5);
    const lift = R + a.distanceTo(b) * 0.55;
    if (mid.lengthSq() < 1e-6) mid.copy(a);
    mid.normalize().multiplyScalar(lift);

    const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
    const pts = curve.getPoints(72);
    const geo = new THREE.BufferGeometry().setFromPoints(pts);
    earthGroup.add(new THREE.Line(geo, routeMat));

    const spark = new THREE.Sprite(new THREE.SpriteMaterial({
      map: dotTex, color: 0xbfe4ff, transparent: true,
      blending: THREE.AdditiveBlending, depthWrite: false
    }));
    spark.scale.setScalar(0.62);
    earthGroup.add(spark);

    routePoints.push({ curve, sprite: spark, t: i / ROUTES.length, speed: 0.1 + i * 0.012 });
  });

  /* ---- 交互：拖拽 / 缩放 / 点击 ------------------------------------- */
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.055;
  controls.enablePan = false;
  controls.rotateSpeed = 0.55;
  controls.zoomSpeed = 0.6;
  /* 缩放范围：能凑近看大陆细节，也能拉远看成一颗小地球 */
  controls.minDistance = 20;
  controls.maxDistance = 120;
  controls.minPolarAngle = 0.35;
  controls.maxPolarAngle = Math.PI - 0.35;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.32;
  controls.target.set(0, 0, 0);

  let idle = 0;
  const wake = () => {
    controls.autoRotate = false;
    idle = 6.0;
  };
  renderer.domElement.addEventListener('pointerdown', wake);
  renderer.domElement.addEventListener('wheel', wake, { passive: true });

  /* 点击：射线命中地球 → 在命中点生成涟漪，并把相机轻轻推近 */
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  const ripples = [];
  let downAt = null;

  renderer.domElement.addEventListener('pointerdown', (e) => {
    downAt = { x: e.clientX, y: e.clientY };
  });

  renderer.domElement.addEventListener('pointerup', (e) => {
    if (!downAt) return;
    const moved = Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y);
    downAt = null;
    if (moved > 6) return;                       // 拖动，不是点击

    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);

    const hit = raycaster.intersectObject(earth, false)[0];
    if (!hit) return;

    /* 涟漪：贴着球面的环，朝法线方向立起来，向外扩散并淡出 */
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.30, 0.36, 64),
      new THREE.MeshBasicMaterial({
        color: 0xbfe6ff, transparent: true, opacity: 0.95,
        blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide
      })
    );
    ring.position.copy(hit.point).multiplyScalar(1.004);
    ring.lookAt(0, 0, 0);
    earthGroup.add(ring);
    ripples.push({ mesh: ring, life: 0 });

    /* 相机微推，给点击一点物理反馈 */
    const from = camera.position.clone();
    const dir = from.clone().sub(controls.target).normalize();
    const to = controls.target.clone().add(dir.multiplyScalar(Math.max(controls.minDistance, camera.position.length() * 0.93)));
    camera.position.copy(from.lerp(to, 0.35));
  });

  /* ---- 尺寸 ---------------------------------------------------------- */
  function resize() {
    const s = size();
    renderer.setSize(s, s, false);
    camera.aspect = 1;
    camera.updateProjectionMatrix();
  }
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(resize).observe(host);
  else window.addEventListener('resize', resize);
  resize();

  /* ---- 循环 ---------------------------------------------------------- */
  const clock = new THREE.Clock();
  let running = true;
  document.addEventListener('visibilitychange', () => {
    running = !document.hidden;
    if (running) clock.getDelta();     // 丢掉切回来时的巨大 delta
  });

  function frame() {
    requestAnimationFrame(frame);
    if (!running) return;

    const dt = Math.min(clock.getDelta(), 0.05);

    if (idle > 0) {
      idle -= dt;
      if (idle <= 0) controls.autoRotate = true;
    }

    /* 地球本体慢转（自转感），航线与网格跟着一起转 */
    earthGroup.rotation.y += dt * 0.045;

    /* 卫星光点绕环走 */
    const a = clock.elapsedTime * 0.42;
    satellite.position.set(Math.cos(a) * RING_A, 0, Math.sin(a) * RING_A);

    /* 航线上的流光 */
    for (const r of routePoints) {
      r.t = (r.t + dt * r.speed) % 1;
      const p = r.curve.getPoint(r.t);
      r.sprite.position.copy(p);
      const fade = Math.sin(r.t * Math.PI);
      r.sprite.material.opacity = 0.25 + fade * 0.75;
    }

    /* 涟漪扩散 */
    for (let i = ripples.length - 1; i >= 0; i--) {
      const rp = ripples[i];
      rp.life += dt / 1.15;
      const k = rp.life;
      rp.mesh.scale.setScalar(1 + k * 11);
      rp.mesh.material.opacity = Math.max(0, 0.95 * (1 - k) ** 1.6);
      if (k >= 1) {
        earthGroup.remove(rp.mesh);
        rp.mesh.geometry.dispose();
        rp.mesh.material.dispose();
        ripples.splice(i, 1);
      }
    }

    controls.update();
    renderer.render(scene, camera);
  }

  /* 纹理就绪后再淡入，避免先露出一个没贴图的灰球；慢网 2.5s 后兜底淡入 */
  let shown = false;
  const reveal = () => {
    if (shown) return;
    shown = true;
    host.classList.add('live');
    document.documentElement.classList.add('has-globe');
  };
  const waitTexture = () => {
    if (dayTex.image && dayTex.image.width) reveal();
    else setTimeout(waitTexture, 120);
  };
  waitTexture();
  setTimeout(reveal, 2500);

  frame();
}

/* ---- 工具 -------------------------------------------------------------- */

/* 经纬度 → 世界坐标（与 three 的等距柱状贴图对齐） */
function latLon(lat, lon, radius) {
  const phi = (90 - lat) * DEG;
  const theta = (lon + 180) * DEG;
  return new THREE.Vector3(
    -radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta)
  );
}

/* 程序生成一个径向渐变光点，用作卫星和流光的 sprite 贴图 */
function glowTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const x = c.getContext('2d');
  const g = x.createRadialGradient(64, 64, 0, 64, 64, 64);
  g.addColorStop(0.0, 'rgba(255,255,255,1)');
  g.addColorStop(0.28, 'rgba(190,228,255,.85)');
  g.addColorStop(0.65, 'rgba(90,160,255,.22)');
  g.addColorStop(1.0, 'rgba(40,90,180,0)');
  x.fillStyle = g;
  x.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
