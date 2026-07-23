import { useMemo, useState } from "react";
import type { Resource, ResourceCategory, ResourceTier } from "./types";
import { CATEGORY_LABELS, TIER_LABELS } from "./types";
import { resources } from "./data/resources";
import SearchBar from "./components/SearchBar";
import FilterBar from "./components/FilterBar";
import ResourceCard, { ResourceDetail } from "./components/ResourceCard";
import "./App.css";

function matchesQuery(resource: Resource, query: string): boolean {
  const q = query.toLowerCase().trim();
  if (!q) return true;

  const haystack = [
    resource.name,
    resource.description,
    resource.category,
    resource.tier,
    ...resource.methods,
    ...resource.locations,
    ...(resource.pals ?? []),
    ...(resource.tips ? [resource.tips] : []),
    ...(resource.crafting?.flatMap((c) => [
      c.station,
      ...c.ingredients.map((i) => i.name),
    ]) ?? []),
  ]
    .join(" ")
    .toLowerCase();

  return q.split(/\s+/).every((word) => haystack.includes(word));
}

export default function App() {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<ResourceCategory | "all">("all");
  const [tier, setTier] = useState<ResourceTier | "all">("all");
  const [selected, setSelected] = useState<Resource | null>(null);

  const filtered = useMemo(() => {
    return resources.filter((r) => {
      if (category !== "all" && r.category !== category) return false;
      if (tier !== "all" && r.tier !== tier) return false;
      return matchesQuery(r, query);
    });
  }, [query, category, tier]);

  return (
    <div className="app">
      <header className="header">
        <div className="header-content">
          <div className="logo">
            <span className="logo-icon" aria-hidden="true">
              ◆
            </span>
            <div>
              <h1>Palworld Companion</h1>
              <p className="tagline">Search resources — how &amp; where to get them</p>
            </div>
          </div>
          <SearchBar value={query} onChange={setQuery} resultCount={filtered.length} />
        </div>
      </header>

      <main className="main">
        <FilterBar
          category={category}
          tier={tier}
          onCategoryChange={setCategory}
          onTierChange={setTier}
          categoryLabels={CATEGORY_LABELS}
          tierLabels={TIER_LABELS}
        />

        <div className="layout">
          <section className="results" aria-label="Resource search results">
            {filtered.length === 0 ? (
              <div className="empty-state">
                <p>No resources match your search.</p>
                <p className="empty-hint">Try a different term like &quot;coal&quot;, &quot;desert&quot;, or &quot;fire&quot;.</p>
              </div>
            ) : (
              <div className="card-grid">
                {filtered.map((resource) => (
                  <ResourceCard
                    key={resource.id}
                    resource={resource}
                    isSelected={selected?.id === resource.id}
                    onSelect={() => setSelected(resource)}
                  />
                ))}
              </div>
            )}
          </section>

          <aside className="detail-panel" aria-label="Resource details">
            {selected ? (
              <ResourceDetail
                resource={selected}
                categoryLabels={CATEGORY_LABELS}
                tierLabels={TIER_LABELS}
                onClose={() => setSelected(null)}
              />
            ) : (
              <div className="detail-placeholder">
                <span className="placeholder-icon" aria-hidden="true">
                  🗺️
                </span>
                <h2>Select a resource</h2>
                <p>Click any card to see how to obtain it and where to find it on Palpagos Islands.</p>
              </div>
            )}
          </aside>
        </div>
      </main>

      <footer className="footer">
        <p>
          Palworld Companion — fan-made reference tool. Game data inspired by{" "}
          <a href="https://palworld.wiki.gg/" target="_blank" rel="noreferrer">
            Palworld Wiki
          </a>
          .
        </p>
      </footer>
    </div>
  );
}
