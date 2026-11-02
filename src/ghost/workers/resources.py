"""Premium Component & Visual Design Library Registry for Ghost Demo Builder.

Provides curated reference metadata, CDN snippets, code injection patterns,
and component usage rules for high-end luxury demos ($1,000+ client grade).
Eliminates generic AI look with elite motion, shaders, and UI primitives.
"""
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

class ComponentResource(BaseModel):
    name: str
    category: str  # "premium_ui", "motion", "shaders", "visuals", "dataviz"
    description: str
    best_used_for: str
    industry_benchmark: str
    cdn_or_url: str
    code_pattern: str

PREMIUM_RESOURCES: Dict[str, ComponentResource] = {
    "kokonut_ui": ComponentResource(
        name="KokonutUI",
        category="premium_ui",
        description="Liquid-glass cards, frosted action bars, and interactive components with zero black-box code.",
        best_used_for="Hero cards and interactive reservation/booking widgets that instantly read 'expensive'.",
        industry_benchmark="Apple iOS 26 / macOS Tahoe Liquid Glass design aesthetic.",
        cdn_or_url="https://kokonutui.com",
        code_pattern="""<!-- Liquid Glass Frosted Card Pattern -->
<div class="backdrop-blur-xl bg-white/10 dark:bg-black/30 border border-white/20 dark:border-white/10 rounded-2xl p-8 shadow-2xl transition-all duration-300 hover:border-amber-400/40 hover:shadow-amber-500/10">
    <div class="absolute inset-0 bg-gradient-to-br from-white/10 via-transparent to-transparent pointer-events-none rounded-2xl"></div>
    <!-- Content -->
</div>"""
    ),
    "magic_ui": ComponentResource(
        name="Magic UI",
        category="premium_ui",
        description="150+ high-end animated effects (Interactive Cobe Globe, Animated Beams, Border Beams, Shimmer Buttons).",
        best_used_for="Interactive VIP amenity showcases, global boutique locations, and animated concierge beams.",
        industry_benchmark="GitHub homepage live-PR Cobe globe and Dub.co animated integration diagrams.",
        cdn_or_url="https://magicui.design",
        code_pattern="""<!-- Shimmer Luxury Reservation CTA Button -->
<button class="relative inline-flex h-12 overflow-hidden rounded-full p-[1px] focus:outline-none transition-transform active:scale-95">
  <span class="absolute inset-[-1000%] animate-[spin_3s_linear_infinite] bg-[conic-gradient(from_90deg_at_50%_50%,#c5a059_0%,#ffffff_50%,#c5a059_100%)]"></span>
  <span class="inline-flex h-full w-full cursor-pointer items-center justify-center rounded-full bg-black/90 px-8 py-2 text-xs uppercase tracking-[0.2em] font-medium text-amber-200/90 backdrop-blur-3xl hover:bg-black/80 transition-colors">
    Reserve Tasting Experience
  </span>
</button>"""
    ),
    "react_bits": ComponentResource(
        name="React Bits",
        category="shaders",
        description="WebGL shader backgrounds (Silk, Iridescence, Living Gradients) and interactive canvas effects.",
        best_used_for="Luxury hero backgrounds to eliminate flat static styling and create fluid living depth.",
        industry_benchmark="Stripe's animated mesh-gradient hero; Igloo Inc Awwwards Site of the Day.",
        cdn_or_url="https://reactbits.dev",
        code_pattern="""<!-- Dynamic Luxury Ambient Gradient Canvas/Backdrop -->
<div class="absolute inset-0 -z-10 overflow-hidden">
  <div class="absolute -top-[40%] -left-[20%] w-[70vw] h-[70vw] rounded-full bg-gradient-to-br from-amber-600/15 via-purple-900/10 to-transparent blur-[120px] animate-pulse"></div>
  <div class="absolute -bottom-[30%] -right-[10%] w-[60vw] h-[60vw] rounded-full bg-gradient-to-tl from-emerald-900/15 via-amber-800/10 to-transparent blur-[140px]"></div>
</div>"""
    ),
    "anime_js": ComponentResource(
        name="Anime.js",
        category="motion",
        description="Lightweight 24KB animation engine for staggered text reveals, layout physics, and animated SVG paths.",
        best_used_for="Staggered menu price reveals, animated luxury monogram signatures, and smooth scroll entry.",
        industry_benchmark="Awwwards Site of the Day portfolios with elegant staggered typography reveals.",
        cdn_or_url="https://cdnjs.cloudflare.com/ajax/libs/animejs/3.2.2/anime.min.js",
        code_pattern="""<script src="https://cdnjs.cloudflare.com/ajax/libs/animejs/3.2.2/anime.min.js"></script>
<script>
document.addEventListener('DOMContentLoaded', () => {
  anime.timeline({easing: 'easeOutExpo'})
    .add({
      targets: '.stagger-reveal',
      translateY: [40, 0],
      opacity: [0, 1],
      delay: anime.stagger(120),
      duration: 1200
    });
});
</script>"""
    ),
    "motion": ComponentResource(
        name="Motion (Framer Motion Engine)",
        category="motion",
        description="Physics-based spring animations for weighted, organic page transitions instead of robotic linear easing.",
        best_used_for="Smooth menu accordion drawers, modal popups, and fluid hover cards.",
        industry_benchmark="Powers every award-winning Framer showcase site with fluid spring physics.",
        cdn_or_url="https://motion.dev",
        code_pattern="""<!-- Spring-weighted interactive card transition CSS -->
<style>
.spring-hover {
  transition: transform 0.6s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.6s cubic-bezier(0.16, 1, 0.3, 1);
}
.spring-hover:hover {
  transform: translateY(-6px) scale(1.01);
}
</style>"""
    ),
    "rive": ComponentResource(
        name="Rive",
        category="motion",
        description="State-machine vector animations that react to cursor coordinates in real time. 90% lighter than Lottie.",
        best_used_for="Interactive cursor-reactive hero seals, 3D room rotators, and living ambiance mascots.",
        industry_benchmark="Figma cursor-reactive hero & Duolingo interactive state machines.",
        cdn_or_url="https://unpkg.com/@rive-app/canvas@2.9.1",
        code_pattern="""<script src="https://unpkg.com/@rive-app/canvas@2.9.1"></script>
<!-- Canvas for interactive cursor-reactive state machine -->
<canvas id="rive-hero-canvas" class="w-full h-full pointer-events-auto"></canvas>"""
    ),
    "limora": ComponentResource(
        name="Limora",
        category="visuals",
        description="High-converting on-brand visual asset generator for luxury hospitality (heroes, food photography, ambiance).",
        best_used_for="Replacing generic off-brand AI images with unified, bespoke color-matched photography.",
        industry_benchmark="Bespoke luxury editorial brand kits and high-end restaurant lookbooks.",
        cdn_or_url="https://limora.ai",
        code_pattern="""<!-- Brand-consistent editorial image container with blur-up placeholder -->
<div class="relative overflow-hidden rounded-xl aspect-[16/10] bg-zinc-900">
  <img src="{image_url}" alt="{alt_text}" loading="lazy" class="w-full h-full object-cover transition-transform duration-700 ease-out hover:scale-105 filter brightness-[0.92] contrast-[1.05]" />
  <div class="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent"></div>
</div>"""
    ),
    "bklit": ComponentResource(
        name="Bklit",
        category="dataviz",
        description="17+ styled chart types with automated animated video rendering and luxury light/dark aesthetics.",
        best_used_for="Sommelier wine vintage rating charts, guest rating infographics, and private dining metrics.",
        industry_benchmark="Linear and Stripe dashboards; Cleo AI Awwwards Site of the Day.",
        cdn_or_url="https://bklit.dev",
        code_pattern="""<!-- Styled Luxury Data Metric Card (Light Default + Dark Adaptive) -->
<div class="border border-stone-300 dark:border-amber-500/20 bg-stone-100/80 dark:bg-zinc-950/60 p-6 rounded-xl flex items-center justify-between transition-colors">
  <div>
    <p class="text-xs uppercase tracking-widest text-stone-500 dark:text-zinc-400">Sommelier Selection</p>
    <p class="text-2xl font-serif text-amber-800 dark:text-amber-200">98.4 Pts</p>
  </div>
  <div class="text-right">
    <span class="inline-block px-2.5 py-1 text-[10px] tracking-wider uppercase font-mono bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-500/30 rounded-full">Vintage 2018</span>
  </div>
</div>"""
    )
}

def get_resource(key: str) -> Optional[ComponentResource]:
    """Retrieve metadata and code pattern for a premium resource."""
    return PREMIUM_RESOURCES.get(key.lower().replace("-", "_").replace(" ", "_"))

def list_all_resources() -> List[Dict[str, str]]:
    """List all available premium components for prompt and tool injection."""
    return [
        {
            "id": key,
            "name": r.name,
            "category": r.category,
            "best_used_for": r.best_used_for,
            "industry_benchmark": r.industry_benchmark
        }
        for key, r in PREMIUM_RESOURCES.items()
    ]

def get_demo_starter_kit(archetype: int = 1) -> str:
    """Returns the ready-to-inject CSS and JS header tags for the luxury components."""
    return """
    <!-- Premium Fonts -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@400;600;700&family=Cormorant+Garamond:ital,wght@0,300;0,500;0,700;1,400&family=Plus+Jakarta+Sans:wght@300;400;500;600&family=Playfair+Display:ital,wght@0,400;0,600;1,400&family=Space+Grotesk:wght@400;600&family=Syne:wght@500;700;800&display=swap" rel="stylesheet">
    
    <!-- Tailwind CDN -->
    <script src="https://cdn.tailwindcss.com"></script>
    
    <!-- Anime.js (Staggered Motion) -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/animejs/3.2.2/anime.min.js"></script>

    <script>
      tailwind.config = {
        darkMode: 'class',
        theme: {
          extend: {
            fontFamily: {
              serif: ['"Playfair Display"', '"Cormorant Garamond"', 'serif'],
              display: ['"Cinzel"', '"Syne"', 'sans-serif'],
              sans: ['"Plus Jakarta Sans"', 'sans-serif'],
              mono: ['"Space Grotesk"', 'monospace'],
            }
          }
        }
      }
    </script>
"""
