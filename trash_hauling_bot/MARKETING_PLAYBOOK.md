# HaulYeah — DFW Marketing Playbook

Copy-paste assets for Facebook Marketplace outreach and Meta (Facebook/Instagram)
ads. The business: a Ford **F-150 + a drop-off container** set on-site and picked
back up, serving the **Dallas–Fort Worth (DFW)** metro.

> The source of truth for this copy is `agents/marketing.py`. Edit there and these
> sentences stay in sync with the `/pitch` and `/ads` Telegram commands.

---

## 1. Marketplace / DM outreach (first touch)

Use when replying to someone who posted a junk-removal / hauling / cleanout need.

**General (auto-localizes to the city when known):**
> Hi! Saw your post about {job type} — we're HaulYeah, a local hauling crew working
> {in your city / across the DFW area}. We run a truck and trailer, handle any size
> load, and do the lifting and disposal for you. We can also drop a container on-site,
> leave it while you load at your own pace, then pick it up — handy for bigger
> cleanouts and renos. Happy to give you a free quote — what are you looking to get
> rid of, and where are you located?

**Container-focused pitch:**
> We drop a container at your place {in your city / across the DFW area}, leave it so
> you can load on your own schedule, then haul it off when you're done. You pay for the
> space you use — no surprise dump fees. Want me to check availability?

In Telegram: `/pitch Plano` (or any DFW city) returns both, ready to send.

---

## 2. Meta ad copy (single image / video)

Paste into Ads Manager → Ad → "Primary text / Headline / Description". CTA in caps
maps to Meta's call-to-action button.

### Ad A — "Junk removal, speed"
- **Primary text:** Junk piling up? HaulYeah clears it out fast. 🛻 We bring the truck
  and a drop-off container, do all the heavy lifting, and haul everything to the dump
  for you — serving all of DFW. Same-week (often same-day) pickup. Free, no-pressure quotes.
- **Headline:** DFW Junk & Trash Hauling
- **Description:** Free quote • Same-week pickup
- **CTA:** Get Quote

### Ad B — "Drop-off container"
- **Primary text:** Doing a cleanout or reno? Skip the dumpster headache. 📦 We drop a
  container at your place, leave it so you can load at your own pace, then pick it up
  when you're done. You only pay for the space you use. Proudly serving the DFW metro.
- **Headline:** Drop-Off Container Service — DFW
- **Description:** No hidden dump fees
- **CTA:** Get Quote

### Ad C — "We haul anything"
- **Primary text:** Garage, estate, foreclosure, or construction debris — if it needs
  to go, HaulYeah hauls it. 💪 One call, we load it, you relax. Honest flat-rate pricing
  and friendly local crew across Dallas–Fort Worth.
- **Headline:** We Haul Anything
- **Description:** Flat-rate • Local DFW crew
- **CTA:** Message Page

---

## 3. Carousel ad (5 cards)

One image per card. `image_hint` is direction for the creative.

| # | Card headline | On-image / caption copy | Image |
|---|---------------|-------------------------|-------|
| 1 | Junk Removal, Done For You | We load it, haul it, and dispose of it. You don't lift a thing. | Crew loading furniture into an F-150 in a driveway |
| 2 | Drop-Off Container | We drop it, you fill it on your schedule, we pick it up. | Trailer container set on a residential driveway |
| 3 | Cleanouts of Any Size | Garage, estate, foreclosure, attic — we clear it all out. | Before/after of a cleared-out garage |
| 4 | Construction & Reno Debris | Drywall, lumber, old fixtures — hauled away fast. | Truck bed loaded with construction debris |
| 5 | Free Quote, Same-Week Pickup | Serving all of DFW. Message us — we'll get you scheduled. | Smiling crew by the truck with HaulYeah branding |

In Telegram: `/ads` prints all of the above.

---

## 4. Targeting suggestions (for Ads Manager)

- **Geo:** Dallas–Fort Worth metro + 25–40 mi radius (Dallas, Fort Worth, Arlington,
  Plano, Irving, Garland, Frisco, McKinney, Denton, Mesquite, Carrollton, Richardson,
  Grand Prairie, Lewisville, Allen, Mansfield).
- **Audience:** homeowners 28–65; interests: home improvement, moving, real estate,
  property management, estate sales; behaviors: recently moved / likely to move.
- **Objective:** Leads or Messages (so replies land in Page inbox / Messenger).
- **Budget tip:** start small per ad set, let Ad A/B/C compete, scale the winner.

> ⚠️ Launching live ads spends real money and needs your Meta ad account + Page.
> The copy above is ready, but pushing it to Ads Manager is a deliberate, owner-approved
> step — see the PR notes for how the ads MCP tooling can create these once you approve.
