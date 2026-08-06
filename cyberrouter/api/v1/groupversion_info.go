// Package v1 contains API Schema definitions for the routing.cyberrouter.io group.
package v1

import (
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/controller-runtime/pkg/scheme"
)

var (
	// GroupVersion is the group+version for RoutingPolicy CRD.
	// Group = routing.cyberrouter.io (项目专属域, 面试标准做法)
	GroupVersion = schema.GroupVersion{Group: "routing.cyberrouter.io", Version: "v1"}

	// SchemeBuilder registers our types into the manager's scheme so
	// controller-runtime knows how to decode/watch them.
	SchemeBuilder = &scheme.Builder{GroupVersion: GroupVersion}

	// AddToScheme adds this group's types to a scheme.
	AddToScheme = SchemeBuilder.AddToScheme
)
