import type { ResourceCategory, ResourceTier } from "../types";

interface FilterBarProps {
  category: ResourceCategory | "all";
  tier: ResourceTier | "all";
  onCategoryChange: (c: ResourceCategory | "all") => void;
  onTierChange: (t: ResourceTier | "all") => void;
  categoryLabels: Record<ResourceCategory, string>;
  tierLabels: Record<ResourceTier, string>;
}

export default function FilterBar({
  category,
  tier,
  onCategoryChange,
  onTierChange,
  categoryLabels,
  tierLabels,
}: FilterBarProps) {
  return (
    <div className="filter-bar">
      <div className="filter-group">
        <label htmlFor="category-filter">Category</label>
        <select
          id="category-filter"
          value={category}
          onChange={(e) => onCategoryChange(e.target.value as ResourceCategory | "all")}
        >
          <option value="all">All categories</option>
          {(Object.entries(categoryLabels) as [ResourceCategory, string][]).map(
            ([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ),
          )}
        </select>
      </div>

      <div className="filter-group">
        <label htmlFor="tier-filter">Progression</label>
        <select
          id="tier-filter"
          value={tier}
          onChange={(e) => onTierChange(e.target.value as ResourceTier | "all")}
        >
          <option value="all">All tiers</option>
          {(Object.entries(tierLabels) as [ResourceTier, string][]).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
