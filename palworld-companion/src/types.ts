export type ResourceCategory =
  | "basic"
  | "ore"
  | "organic"
  | "refined"
  | "pal_drop"
  | "late_game"
  | "consumable";

export type ResourceTier = "early" | "mid" | "late" | "endgame";

export interface CraftRecipe {
  station: string;
  output?: number;
  ingredients: { name: string; amount: number }[];
}

export interface Resource {
  id: string;
  name: string;
  category: ResourceCategory;
  tier: ResourceTier;
  description: string;
  methods: string[];
  locations: string[];
  tips?: string;
  pals?: string[];
  crafting?: CraftRecipe[];
  sellPrice?: number;
}

export const CATEGORY_LABELS: Record<ResourceCategory, string> = {
  basic: "Basic Materials",
  ore: "Ores & Minerals",
  organic: "Organic & Plants",
  refined: "Refined Materials",
  pal_drop: "Pal Drops",
  late_game: "Late Game",
  consumable: "Consumables",
};

export const TIER_LABELS: Record<ResourceTier, string> = {
  early: "Early Game",
  mid: "Mid Game",
  late: "Late Game",
  endgame: "Endgame",
};
