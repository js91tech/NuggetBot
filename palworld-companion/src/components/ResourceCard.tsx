import type { Resource, ResourceCategory, ResourceTier } from "../types";

const CATEGORY_ICONS: Record<ResourceCategory, string> = {
  basic: "🪵",
  ore: "⛏️",
  organic: "🌿",
  refined: "⚙️",
  pal_drop: "🐾",
  late_game: "✨",
  consumable: "🧪",
};

interface ResourceCardProps {
  resource: Resource;
  isSelected: boolean;
  onSelect: () => void;
}

export default function ResourceCard({ resource, isSelected, onSelect }: ResourceCardProps) {
  return (
    <button
      type="button"
      className={`resource-card${isSelected ? " selected" : ""}`}
      onClick={onSelect}
      aria-pressed={isSelected}
    >
      <span className="card-icon" aria-hidden="true">
        {CATEGORY_ICONS[resource.category]}
      </span>
      <div className="card-body">
        <h3>{resource.name}</h3>
        <p className="card-desc">{resource.description}</p>
        <div className="card-tags">
          <span className={`tag tier-${resource.tier}`}>{resource.tier}</span>
          <span className="tag">{resource.locations[0]}</span>
        </div>
      </div>
    </button>
  );
}

interface ResourceDetailProps {
  resource: Resource;
  categoryLabels: Record<ResourceCategory, string>;
  tierLabels: Record<ResourceTier, string>;
  onClose: () => void;
}

export function ResourceDetail({
  resource,
  categoryLabels,
  tierLabels,
  onClose,
}: ResourceDetailProps) {
  return (
    <article className="resource-detail">
      <div className="detail-header">
        <div>
          <h2>{resource.name}</h2>
          <div className="detail-meta">
            <span className={`tag tier-${resource.tier}`}>{tierLabels[resource.tier]}</span>
            <span className="tag">{categoryLabels[resource.category]}</span>
            {resource.sellPrice != null && (
              <span className="tag price">Sell: {resource.sellPrice}g</span>
            )}
          </div>
        </div>
        <button type="button" className="close-btn" onClick={onClose} aria-label="Close details">
          ×
        </button>
      </div>

      <p className="detail-description">{resource.description}</p>

      <section>
        <h3>How to Get</h3>
        <ul>
          {resource.methods.map((method) => (
            <li key={method}>{method}</li>
          ))}
        </ul>
      </section>

      <section>
        <h3>Where to Find</h3>
        <ul className="location-list">
          {resource.locations.map((loc) => (
            <li key={loc}>
              <span className="loc-pin" aria-hidden="true">
                📍
              </span>
              {loc}
            </li>
          ))}
        </ul>
      </section>

      {resource.pals && resource.pals.length > 0 && (
        <section>
          <h3>Related Pals</h3>
          <ul className="pal-list">
            {resource.pals.map((pal) => (
              <li key={pal}>{pal}</li>
            ))}
          </ul>
        </section>
      )}

      {resource.crafting && resource.crafting.length > 0 && (
        <section>
          <h3>Crafting Recipes</h3>
          {resource.crafting.map((recipe) => (
            <div key={recipe.station} className="recipe">
              <p className="recipe-station">
                <strong>{recipe.station}</strong>
                {recipe.output ? ` → makes ${recipe.output}` : ""}
              </p>
              <ul className="ingredient-list">
                {recipe.ingredients.map((ing) => (
                  <li key={ing.name}>
                    {ing.amount}× {ing.name}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </section>
      )}

      {resource.tips && (
        <section className="tips-section">
          <h3>💡 Tip</h3>
          <p>{resource.tips}</p>
        </section>
      )}
    </article>
  );
}
