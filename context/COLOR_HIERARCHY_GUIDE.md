# COLOR HIERARCHY GUIDE

Color does three jobs: show depth, show importance, and show meaning. Give each job its own tool so they never compete.

## 1. Layers (depth)
An all-white screen has nowhere to put depth. Cards melt into the page, so borders and shadows end up doing all the work.

LIGHT MODE
- Canvas (page): #F2F2F7, the gray iOS puts behind grouped lists
- Surface (cards, panels): #FFFFFF, kept for what sits on top
- Raised (menus, modals, popovers): #FFFFFF with a bigger shadow

DARK MODE
- Canvas: #121212
- Surface: #1E1E1E
- Raised: #2A2A2A

Rule: never use one flat color for canvas and surface. Two steps of lightness is enough.

## 2. Elevation
Let shadows do the lifting in light mode. In dark mode a lighter surface means higher. In light mode the surfaces stay white and the shadow grows instead.
- Light: resting 0 1px 2px rgba(0,0,0,.06) / hover 0 8px 24px rgba(0,0,0,.10) / modal 0 24px 64px rgba(0,0,0,.16). Layer two soft shadows; never one harsh one.
- Dark: shadows barely show on dark, so raise by stepping up one surface color. Add a 1px border at about 8% white if edges blur.
- Use a border only where two same-color surfaces touch, as a hairline at 6-8% ink.

## 3. Text
Soften your black. Pure #000 on pure white vibrates.
- Primary: #1D1B20 (about 17:1 on white)
- Secondary: #49454F (about 9:1 on white), for captions, labels and helper text
- Disabled: the same ink at 38 percent, only for disabled. Never use it for text people need to read.
- Dark mode: primary #E6E1E5, secondary #CAC4D0, disabled the same at 38%.
- Hierarchy comes from size and weight first, then from these two tones. Do not invent a third gray.

## 4. The accent (the 10%)
One accent only. It marks the primary action, links, focus rings, and selected state. If everything is accent, nothing is.
- Deepen it on light. #BB86FC almost disappears on white (about 2.6:1), while #6200EE is easy to see (about 7.6:1).
- Lighten it on dark. Use #BB86FC on #121212 (about 7:1).
- So the accent is a pair, not one value. Always test it on both themes.
- States: hover is 8% darker (lighter on dark), pressed is 12%, focus is a 2px ring in the accent at 40% outside the element.
- Text on an accent fill must be white (light mode) or near-black (dark mode) and still pass 4.5:1.
- Tint, don't shout: for a secondary accent surface, use the accent at 8-12% on the card color.

## 5. Proportion
60 / 30 / 10
- 60% canvas and big neutral areas
- 30% surfaces, cards, text
- 10% accent
If a screen has more than about 10% accent, pull it back.

## 6. Status colors (meaning, not decoration)
Success, warning and danger are used only for state, and never beside the accent as decoration.
- Light: success #1B7F4B / warning #A15C00 / danger #BA1A1A
- Dark: success #6DD58C / warning #FFB95C / danger #FFB4AB
- Never rely on color alone: pair each with an icon or a label.
- Tint backgrounds at 10-12% of the status color, with the full color for text and icons.

## 7. Tokens (define once, use only these)
```css
:root {
  --canvas: #F2F2F7;
  --surface: #FFFFFF;
  --raised: #FFFFFF;
  --ink: #1D1B20;
  --ink-2: #49454F;
  --ink-disabled: rgba(29, 27, 32, .38);
  --accent: #6200EE;
  --on-accent: #FFFFFF;
  --line: rgba(29, 27, 32, .08);
  --shadow-resting: 0 1px 2px rgba(0, 0, 0, .06);
  --shadow-hover: 0 8px 24px rgba(0, 0, 0, .10);
  --shadow-modal: 0 24px 64px rgba(0, 0, 0, .16);
}
:root[data-theme="dark"], html.dark {
  --canvas: #121212;
  --surface: #1E1E1E;
  --raised: #2A2A2A;
  --ink: #E6E1E5;
  --ink-2: #CAC4D0;
  --ink-disabled: rgba(230, 225, 229, .38);
  --accent: #BB86FC;
  --on-accent: #1D1B20;
  --line: rgba(255, 255, 255, .08);
}
```
No hex values in components. Components reference tokens only.

## 8. Checks before shipping
- Body text 4.5:1 or better, large text 3:1, UI borders and icons 3:1.
- Squint test: blur the screenshot. You should still see the card edges, the one primary button, and the reading order.
- Grayscale test: the hierarchy should survive with no color at all.
- Check both themes on a real phone, not just on a monitor.

## Common mistakes
- Pure white page with white cards, then a border on everything.
- Pure black text.
- The same accent value in light and dark.
- Three or more accent colors, or accent used for decoration.
- Disabled gray used for real body text.
- Gradients or glass behind small text.
