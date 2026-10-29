import { Link } from "react-router";

import { PageHeader } from "@/components/common/PageHeader";
import { Panel } from "@/components/common/Panel";
import { EmptyState } from "@/components/common/QueryState";

export function NotFound() {
  return (
    <>
      <PageHeader title="Page not found" />
      <Panel>
        <EmptyState
          message="There's nothing at this address."
          hint={<Link className="text-blue-strong underline" to="/">Back to the overview</Link>}
        />
      </Panel>
    </>
  );
}
