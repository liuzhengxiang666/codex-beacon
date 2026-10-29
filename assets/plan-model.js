/* Pure display model. Dependencies describe the plan, never start parallel agents. */
(() => {
  "use strict";
  function nodeState(node) {
    const issues = (node.issues || []).filter(issue => issue.status !== "resolved");
    const blocker = node.status === "blocked" || issues.some(issue => issue.kind === "blocker");
    const decision = issues.some(issue => issue.kind === "decision");
    const verification = issues.some(issue => issue.kind === "verification");
    const branch = issues.some(issue => issue.kind === "branch");
    return {
      attention: blocker ? "blocker" : decision ? "decision" : verification ? "verification" : null,
      label: blocker ? (node.status === "blocked" ? "受阻" : "有卡点") : decision ? "待决定" :
        node.needsReview ? "待修订" : verification ? "待验收" : branch ? "有分支" : null,
      waitingVerification: verification && !blocker && !decision && !branch && !node.needsReview,
      active: node.status === "in_progress" && !blocker && !decision && !verification,
    };
  }
  function parallelCount(nodes) {
    const byId = new Map(nodes.map(node => [node.id, node]));
    return nodes.filter(node => nodeState(node).active &&
      (!Array.isArray(node.dependsOn) || node.dependsOn.every(id => byId.get(id)?.status === "completed"))).length;
  }
  function interventionText(plan, nodeId, issueId) {
    const index = plan.nodes.findIndex(node => node.id === nodeId);
    const node = plan.nodes[index];
    if (!node) throw new Error("节点已更新，请查看最新状态。");
    const context = `@执行面板\n当前聊天：${plan.sessionId}\nPlan v${plan.planVersion} · 第 ${String(index + 1).padStart(2, "0")} 项「${node.title}」\n节点 ID：${node.id}`;
    if (issueId === undefined) {
      const labels = { pending: "待开始", in_progress: "进行中", completed: "已完成", blocked: "受阻", cancelled: "已取消" };
      const lines = [context, `节点状态：${labels[node.status] || "待开始"}`];
      const state = nodeState(node);
      if (state.label) lines.push(`执行情况：${state.label}`);
      if (node.summary) lines.push(`节点说明：${node.summary}`);
      if (node.acceptance) lines.push(`验收要求：${node.acceptance}`);
      return `${lines.join("\n")}\n\n以上为计划快照上下文。我的干预要求：\n`;
    }
    const issue = node?.issues?.find(value => value.id === issueId && value.status !== "resolved");
    if (!issue) throw new Error("事项已更新，请查看最新状态。");
    const kindLabel = issue.kind === "verification" ? "待验收" : issue.kind === "decision" ? "待决定" : issue.kind === "branch" ? "分支" : "卡点";
    return `${context}\n问题 ID：${issue.id}\n${kindLabel}：${issue.title}\n${issue.detail || ""}\n\n以上为计划快照上下文。我的干预要求：\n`;
  }
  window.ExecutionPlanModel = Object.freeze({ nodeState, parallelCount, interventionText });
})();
