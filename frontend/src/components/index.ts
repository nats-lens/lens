/** The design system's front door.
 *
 * Screens import from "@/components" and get the whole vocabulary. Anything not
 * exported here is either private to a component or not ready to be built on.
 */

// Provenance -- the components that carry the product's central claim.
export { SourceBadge } from "./SourceBadge";
export { SourcedValue, hasValue, type SourcedLike } from "./SourcedValue";
export { EmptyState, NoRows, type UnavailableLike } from "./EmptyState";
export { StatCard } from "./StatCard";
export { ErrorPanel } from "./ErrorPanel";
export { DroppedRow } from "./DroppedRow";

// Shell.
export { Shell, AppFrame, Sidebar, Header, type ShellServer, type NavKey, type NavPath } from "./Shell";

// Primitives.
export { Button, buttonVariants, type ButtonProps } from "./ui/button";
export { Input, Textarea, Field, type InputProps } from "./ui/input";
export { Select, type SelectProps } from "./ui/select";
export { Toggle } from "./ui/toggle";
export { Badge, badgeVariants, type BadgeProps, type BadgeTone } from "./ui/badge";
export { Card, CardHeader, CardBody, FactRow } from "./ui/card";
export { Tabs, type Tab } from "./ui/tabs";
export { Mono, Figure } from "./ui/mono";
export { StatusDot, toneForState, type Tone } from "./ui/status-dot";
export { Meter } from "./ui/meter";
export { SubjectChip } from "./ui/subject-chip";
export { DataTable, type Column } from "./ui/data-table";
export { Split, SplitMain, ListPane, ListRow } from "./ui/list-pane";
export { Page, PageHeader, Section } from "./ui/layout";
