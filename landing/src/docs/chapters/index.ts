import type { Chapter } from "../kit";
import { admin, keys, tokens } from "./manage";
import { models } from "./models";
import { operate } from "./operate";
import { basics, install, workspaces } from "./start";
import { workflows } from "./workflows";

/** The guide in reading order; the sidebar groups them by `group`. */
export const chapters: Chapter[] = [install, basics, workspaces, models, workflows, operate, keys, tokens, admin];
