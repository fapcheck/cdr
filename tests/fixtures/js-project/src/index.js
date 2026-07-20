import chunk from "lodash/chunk";
import { liveFeature } from "./live-feature.js";

export function run(items) {
  return liveFeature(chunk(items, 2));
}
