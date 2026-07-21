import { live } from "@core/live";
import { feature } from "./barrel";

void import("./lazy-worker");
console.log(live, feature);
